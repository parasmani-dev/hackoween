"""Run one operator-started rover command."""

import base64
import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import mkdtemp

from PIL import Image

from .claude import ClaudeClient, ClaudeError
from .engine import ActionOutcomeUncertain, EngineClient, EngineError


MAX_DECISIONS = 60
SYSTEM_PROMPT = (
    "Control the rover to complete the operator's fetch command. Choose exactly one action "
    "per decision. Use the front camera to identify and approach the requested object, "
    "pick it up, and carry it into the drop zone before releasing it. Use short, deliberate "
    "moves near objects and walls. After a collision, use the updated view to choose a "
    "clear route. Aim to finish within 25 decisions. Treat the supplied history as observations."
)


@dataclass(frozen=True)
class RunResult:
    status: str
    decisions: int
    usage: dict[str, int]
    run_dir: Path


def _capture(engine, run_dir, index):
    name = f"{index:04d}.png"
    try:
        with Image.open(BytesIO(engine.look()), formats=["JPEG"]) as image:
            if image.size != (640, 480):
                raise ValueError("Unexpected rover camera dimensions.")
            pixels = image.convert("RGB")
            pixels.info.clear()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise EngineError("Could not decode rover camera.") from None
    path = run_dir / name
    pixels.save(path, format="PNG")
    source = {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
    }
    return name, {"type": "image", "source": source}


def _terminal_status(state):
    if state["state"] == "Done":
        return "engine_done"
    if state["state"] == "Blocked" and state["retry_count"] >= 3:
        return "recovery_exhausted"
    return None


def run_command(command, *, engine=None, claude=None, output_root="runs", max_decisions=MAX_DECISIONS):
    """Return why the agent stopped. The operator owns evaluation and lifecycle."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Provide a fetch command.")
    if type(max_decisions) is not int or not 1 <= max_decisions <= MAX_DECISIONS:
        raise ValueError("Decision limit must be between 1 and 60.")
    engine = EngineClient() if engine is None else engine
    claude = ClaudeClient() if claude is None else claude
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(mkdtemp(prefix="run-", dir=root))
    history = deque(maxlen=8)
    usage = {"input_tokens": 0, "output_tokens": 0}
    decisions = 0
    status = "decision_limit"

    try:
        with (run_dir / "steps.jsonl").open("x", encoding="utf-8") as journal:
            state = engine.state()
            terminal = _terminal_status(state)
            if terminal:
                return RunResult(terminal, decisions, usage, run_dir)
            frame_name, frame = _capture(engine, run_dir, 0)
            for step in range(1, max_decisions + 1):
                context = json.dumps({
                    "command": command,
                    "state": state,
                    "remaining_decisions": max_decisions - step + 1,
                    "recent_actions": list(history),
                }, separators=(",", ":"))
                decisions = step
                decision = claude.decide(
                    [{"role": "user", "content": [{"type": "text", "text": context}, frame]}],
                    system=SYSTEM_PROMPT,
                )
                for key, count in decision.usage.items():
                    usage[key] = usage.get(key, 0) + count

                timestamp = datetime.now(timezone.utc).isoformat()
                try:
                    result = engine.execute(decision.name, decision.args)
                except ActionOutcomeUncertain:
                    result = {"outcome": "uncertain"}
                    status = "action_uncertain"
                action = {"tool": decision.name, "args": decision.args, "result": result}
                journal.write(json.dumps({
                    "step": step, "timestamp": timestamp, **action, "frame_path": frame_name,
                }) + "\n")
                journal.flush()
                if status == "action_uncertain":
                    break
                history.append(action)
                if decision.name in ("move", "turn"):
                    frame_name, frame = _capture(engine, run_dir, step)
                state = engine.state()
                terminal = _terminal_status(state)
                if terminal:
                    status = terminal
                    break
    except ClaudeError:
        status = "claude_error"
    except EngineError:
        status = "engine_error"
    except OSError:
        status = "artifact_error"
    return RunResult(status, decisions, usage, run_dir)
