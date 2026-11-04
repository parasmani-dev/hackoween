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
from .openai_client import OpenAIClient, OpenAIError


MAX_DECISIONS = 60
SYSTEM_PROMPT = (
    "Control the rover to complete the operator's fetch command. Choose one action per decision. "
    "Use the latest front camera image as your only source for object and zone locations. "
    "Match both color and silhouette: spheres look round, cubes have corners, cylinders "
    "have upright sides, and cones taper to a tip. "
    "Aim toward the target: image left means a negative turn and image right means a positive turn. "
    "A target in the middle third is aligned enough. Advance about 0.3 to 0.5 metres along "
    "clear floor and reassess. If a turn moved the target farther from center, correct once "
    "in the opposite direction, then move toward it when sufficiently aligned. "
    "Avoid long blind drives. When the target is close, use 0.1 to 0.2 metre moves. "
    "Pick up only when the requested object is centered and within 0.5 metres. "
    "If pickup fails, use the frame to adjust distance or alignment before trying again. "
    "If the target is out of view, scan consistently in one direction in 45 degree turns. "
    "If several turns have not helped, change viewpoint by moving a short distance into clear floor. "
    "Do not use zero turns or alternate opposite turns without making progress. "
    "When holding, center the green drop zone, approach in short steps, and drop when close enough "
    "that the carried object will land inside it. "
    "When Blocked, check the recent collided move. If you still face the same way, first move "
    "0.3 metres in the opposite direction of that move to gain clearance. If you have already "
    "turned, use the current camera to choose a short move into clear floor. After gaining clearance, "
    "turn away from the obstacle and follow a clear route. Do not retry toward the same obstacle. "
    "Use recent actions and their results to avoid repeating failed approaches."
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
    if state["state"] in ("Blocked", "Done") and state["retry_count"] >= 3:
        return "recovery_exhausted"
    if state["state"] == "Done":
        return "engine_done"
    return None


def run_command(
    command, *, engine=None, claude=None, output_root="runs",
    max_decisions=MAX_DECISIONS, audit_requests=False, provider="claude",
):
    """Return why the agent stopped. The operator owns evaluation and lifecycle."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Provide a fetch command.")
    if type(max_decisions) is not int or not 1 <= max_decisions <= MAX_DECISIONS:
        raise ValueError("Decision limit must be between 1 and 60.")
    engine = EngineClient() if engine is None else engine
    if audit_requests and claude is not None:
        raise ValueError("Configure auditing on the supplied Claude client.")
    clients = {"claude": ClaudeClient, "openai": OpenAIClient}
    if provider not in clients:
        raise ValueError("Choose the claude or openai provider.")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(mkdtemp(prefix="run-", dir=root))

    def audit_request(payload):
        with (run_dir / f"{provider}_requests.jsonl").open("a", encoding="utf-8") as audit:
            audit.write(json.dumps(payload, allow_nan=False) + "\n")

    if claude is None:
        claude = clients[provider](audit_request=audit_request if audit_requests else None)
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
    except OpenAIError:
        status = "openai_error"
    except EngineError:
        status = "engine_error"
    except OSError:
        status = "artifact_error"
    return RunResult(status, decisions, usage, run_dir)
