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
MAX_REJECTED_TURNS = 3
MAX_TURNS_WITHOUT_PROGRESS = 12
SYSTEM_PROMPT = (
    "Control the rover to complete the operator's fetch command. Choose one action per decision. "
    "Use the latest front camera image as your only source for object and zone locations. "
    "Match both color and silhouette: spheres look round, cubes have corners, cylinders "
    "have upright sides, and cones taper to a tip. "
    "Before choosing an action, locate the requested object in this image. While not holding, "
    "ignore the drop zone until pickup succeeds. Do not drive straight ahead when the target "
    "is visibly off-center. The 640-pixel image has a 90-degree horizontal field of view: "
    "a target near x=160 needs roughly -27 degrees; x=240 about -14 degrees; "
    "x=320 needs no turn; x=400 about +14 degrees; x=480 about +27 degrees. "
    "For a visible target use a small correction, usually 5 to 25 degrees, never a 90-degree "
    "search turn. Reobserve after that correction. Do not undo a turn just because a different "
    "object is now centered. Track the requested color AND shape. "
    "Aim toward the target: image left means a negative turn and image right means a positive turn. "
    "A target in the middle third is aligned enough. Advance about 0.3 to 0.5 metres along "
    "clear floor and reassess. If a turn moved the target farther from center, correct once "
    "in the opposite direction, then move toward it when sufficiently aligned. "
    "Avoid long blind drives. When the target is close, use 0.1 to 0.2 metre moves. "
    "Pick up only when the requested object is centered and within 0.5 metres. "
    "A close object can be partly below the bottom edge of the image. After a short approach "
    "to a centered close target, try pickup instead of driving over it or starting a new scan. "
    "A failed pickup is a useful distance check; approach another 0.1 metres only if clear. "
    "If pickup fails, use the frame to adjust distance or alignment before trying again. "
    "If the target is out of view, scan consistently in one direction in 45 degree turns. "
    "If a full scan has not helped, change viewpoint only when the camera shows clear floor. "
    "If the image is mostly a nearby wall, reverse 0.2 metres rather than driving into it. "
    "Do not use zero turns or alternate opposite turns without making progress. "
    "When holding, use the command and camera to locate the drop zone, center it, "
    "approach in short steps, and drop when close enough "
    "that the carried object will land inside it. "
    "When Blocked, use recent action results to identify the contact. After a failed turn, "
    "the rover may have rotated partly. Use the refreshed camera to choose a short move into "
    "clear floor if safe. If a move is unsafe, turn away from the contact if the view supports it. "
    "Do not repeat the failed turn. After a collided move, if still facing the same way, move "
    "0.3 metres in the opposite direction of that move to gain clearance. If the heading changed, "
    "use the current camera to choose a safe short move. Then follow a clear route. "
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


def _navigation_prompt(history):
    turns = []
    for action in reversed(history):
        if action["tool"] != "turn" or not action["result"]["success"]:
            break
        turns.append(action["args"]["degrees"])
        if len(turns) == 3:
            break
    if len(turns) == 3 and turns[0] * turns[1] < 0 and turns[1] * turns[2] < 0:
        return SYSTEM_PROMPT + (
            " Your last three successful turns alternated direction without translating. "
            "Stop fine alignment. If the target is close enough and aligned, pick up now. "
            "If the current image shows clear floor toward the target, move forward 0.2 metres "
            "and reassess. If the path is blocked, use the collision recovery instructions. "
            "If the target is out of view and a move is unsafe, scan in the same direction "
            "as the last turn. Do not immediately reverse the last turn again."
        )
    return SYSTEM_PROMPT


def _recovery_prompt(history):
    """Use action outcomes only; never use evaluator identities or coordinates."""
    if not history:
        return ""
    last = history[-1]
    if last["tool"] == "move" and last["result"].get("collided"):
        retreat = -0.3 if last["args"]["distance"] > 0 else 0.3
        return (f" The last move collided. The simulator already stopped and backed off 0.1 m. "
                f"Your heading has not changed since that move. Gain clearance with move({retreat}) "
                "before another approach; do not continue driving in the collision direction.")
    if last["tool"] == "pickup" and last["result"].get("success"):
        return " Pickup succeeded. Now find the requested drop zone in the camera; do not search for the object again."
    if last["tool"] == "pickup" and not last["result"].get("success"):
        return " Pickup failed. Keep tracking the same target; correct alignment or approach by at most 0.1 m if clear."
    return ""


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
    rejected_turns = 0
    turns_without_progress = 0

    try:
        with (run_dir / "steps.jsonl").open("x", encoding="utf-8") as journal:
            state = engine.state()
            terminal = _terminal_status(state)
            if terminal:
                return RunResult(terminal, decisions, usage, run_dir)
            frame_name, frame = _capture(engine, run_dir, 0)

            def log_decision(step, timestamp, tool, args, result, frame_name):
                journal.write(json.dumps({
                    "step": step, "timestamp": timestamp, "tool": tool, "args": args,
                    "result": result, "frame_path": frame_name,
                }) + "\n")
                journal.flush()

            for step in range(1, max_decisions + 1):
                context = json.dumps({
                    "command": command,
                    "state": state,
                    "remaining_decisions": max_decisions - step + 1,
                    "recent_actions": list(history),
                }, separators=(",", ":"))
                decisions = step
                system = _navigation_prompt(history)
                alternating_turns = system != SYSTEM_PROMPT
                system += _recovery_prompt(history)
                if rejected_turns:
                    system += (
                        " The previous action proposal was rejected before dispatch. "
                        "The rover has not moved. Choose a different action using the same image."
                    )
                try:
                    decision = claude.decide(
                        [{"role": "user", "content": [{"type": "text", "text": context}, frame]}],
                        system=system,
                    )
                except (ClaudeError, OpenAIError):
                    log_decision(step, datetime.now(timezone.utc).isoformat(), None, {},
                                 {"outcome": "not_dispatched", "reason": "provider_error"}, frame_name)
                    raise
                for key, count in decision.usage.items():
                    usage[key] = usage.get(key, 0) + count

                timestamp = datetime.now(timezone.utc).isoformat()
                repeats_collision = (
                    decision.name == "move" and history and history[-1]["tool"] == "move"
                    and history[-1]["result"].get("collided")
                    and decision.args["distance"] * history[-1]["args"]["distance"] > 0
                )
                if repeats_collision or (decision.name == "turn" and (
                    turns_without_progress >= MAX_TURNS_WITHOUT_PROGRESS
                    or (alternating_turns
                        and decision.args["degrees"] * history[-1]["args"]["degrees"] < 0)
                )):
                    log_decision(step, timestamp, decision.name, decision.args,
                                 {"outcome": "not_dispatched", "reason": "navigation_guard"}, frame_name)
                    rejected_turns += 1
                    if rejected_turns >= MAX_REJECTED_TURNS:
                        status = "navigation_stalled"
                        break
                    continue

                rejected_turns = 0
                try:
                    result = engine.execute(decision.name, decision.args)
                except ActionOutcomeUncertain:
                    result = {"outcome": "uncertain"}
                    status = "action_uncertain"
                action = {"tool": decision.name, "args": decision.args, "result": result}
                log_decision(step, timestamp, decision.name, decision.args, result, frame_name)
                if status == "action_uncertain":
                    break
                history.append(action)
                if decision.name == "turn":
                    turns_without_progress += 1
                elif result["success"] and (
                    decision.name != "move" or decision.args["distance"] != 0
                ):
                    turns_without_progress = 0
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
