"""Run one operator-started rover command."""

import base64
import json
import math
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
from .tools import finite_number
from .visual_control import correct_action


MAX_DECISIONS = 60
MAX_REJECTED_TURNS = 3
MAX_TURNS_WITHOUT_PROGRESS = 12
SYSTEM_PROMPT = (
    "Control the rover to complete the operator's fetch command. Choose one action per decision. "
    "Use only the latest front camera image to locate objects, obstacles and the requested drop zone. "
    "Match both color and silhouette: spheres are round, cubes have corners, cylinders have upright "
    "sides and cones taper to a tip. While not holding, first approach and pick up the requested object. "
    "Only after pickup succeeds, navigate to the requested zone. "
    "Avoid all contact, including with the requested target: pickup is a reach action, not a bump. "
    "When depth is present it is the nearest surface on one forward ray from a sensor 0.27 m "
    "ahead of the rover center. Leave 0.04 m measured clearance. It gives no object identity "
    "and does not cover the sides of the chassis. Check the full visible swept path and turn "
    "toward open floor if another object blocks it. If the selected target is centered and "
    "depth is at most 0.08 m, try pickup BEFORE any further forward movement. "
    "If the target is already visible, use the direct small alignment correction, not a "
    "45-degree search turn that overshoots it. "
    "Return target_view for the CURRENT image with every action. When holding, box must "
    "describe the requested floor zone; set box=null and floor_zone=false when it is not "
    "visible. Never substitute the carried object or wall stripe. The controller checks "
    "your proposed action against these image bounds and depth and logs any correction. "
    "VISUAL CHECK: begin visual_observation with estimated target pixel bounds "
    "[left,top,right,bottom] in the 640x480 image, or absent/occluded. Then name its "
    "color and silhouette. Check the numeric midpoint before calling it centered: "
    "x below 256 is LEFT, x above 384 is RIGHT. Correct the bearing before advancing. "
    "Do not label a large lower-left object centered just because it occupies much of the image. "
    "When the forward ray hits the aligned target, depth above 0.08 m means continue "
    "a safe approach, not another premature pickup trial. Depth several metres away "
    "while a large target is close means the ray MISSES it: align to the visible target. "
    "TARGET CONTINUITY: write a concise visual_observation with each action. The prior note "
    "is your own uncertain observation, not simulator truth. Keep tracking the SAME requested "
    "color and shape across images. If forward approach makes the target disappear BELOW "
    "the camera, it is probably close beneath the view: do not start a search or turn away. "
    "Use your previous alignment, depth and small safe forward corrections, then pickup. "
    "A failed pickup does not mean the target was misidentified or disappeared. Only abandon "
    "the approach when the new image actually contradicts the match or shows an obstacle. "
    "Do not spend calls centering a target already within the pickup heading tolerance. "
    "ALIGNMENT: image left means a negative turn, right a positive turn. For the 640-pixel, "
    "90-degree horizontal field of view, x=160 corresponds to about -27 degrees, x=240 to -14, "
    "x=320 to zero, x=400 to +14 and x=480 to +27. If the target is off-center, make a small "
    "5-to-25-degree correction before advancing. A target in the middle third is aligned enough. "
    "After each movement, reassess its position using the new image; don't undo a turn to center "
    "a different object. If the requested target is absent, scan consistently in one direction "
    "with 45-degree turns. Don't alternate opposite turns without translating. "
    "DISTANCE: pickup reach is 0.5 metres from the ROVER CENTER, not from the camera. "
    "The camera is 0.22 metres ahead of that center, 0.5 metres above the floor and tilted "
    "10 degrees down. A large object, or an object beginning to clip the bottom edge, can still "
    "be out of reach. In this camera, even a floor contact point at the bottom edge is roughly "
    "0.69 metres from the rover center, beyond pickup reach. Look at the target's floor contact "
    "and the clear floor below it rather than treating its visible size as a distance sensor. "
    "APPROACH: while the target is centered with clear floor in front of it, advance 0.2-to-0.3 "
    "metres and reassess. Once its lower part is below the image, use 0.1-metre approaches "
    "and a pickup trial when aligned. Near reach, reduce to 0.05-metre approaches between "
    "pickup trials so you do not skip the narrow safe approach distance. Do not drive over "
    "an object or take long blind drives. "
    "FAILED PICKUP: this is evidence the current distance or alignment is wrong. Repeating pickup "
    "at the unchanged pose cannot help and will be rejected. If still centered with clear floor, "
    "approach farther; if off-center, make a small alignment correction. Do not reverse away "
    "from a centered target merely because pickup failed. Pickup/drop do not refresh the image; "
    "only move or turn provides a new camera view. "
    "CARRY AND DROP: when holding, find the requested zone in the image, face it and advance "
    "along clear floor in short steps. Identify the zone as a bounded patch ON THE FLOOR, "
    "not the colored stripe on a vertical wall. Check visible floor boundaries and perspective. "
    "Keep that zone's pixel bounds in visual_observation after pickup. A distant colored patch is not close enough. Approach "
    "until the carried object will be over the zone, then drop. If a drop is out of zone, "
    "the object has been released: pick it up again before trying another delivery. "
    "The carried object can obscure the bottom of the camera; it is already attached, not "
    "a new target to approach. If another object blocks the route to the zone, use visible "
    "clear floor to go around it rather than driving through it. "
    "A failed drop at the same pose must not be retried without correcting position. "
    "RECOVERY: if the image is mostly a nearby wall, reverse 0.2 metres rather than driving "
    "into it. After a collided move, the simulator stopped and backed off 0.1 metres. If heading "
    "is unchanged, move opposite that failed move for clearance, then use a clear route. "
    "If contact occurred during a final approach to the centered target, the automatic backoff "
    "may already provide clearance: try pickup once; if it fails, realign and use a 0.05-metre "
    "approach rather than repeating the larger collided drive. A blocked turn can rotate "
    "partly: use the refreshed camera to choose safe clearance. "
    "Do not repeat the failed turn. Use recent results to avoid repeating failed approaches."
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
                "Your heading has not changed since that move. If contact was with the centered "
                "requested target during its final approach, try pickup once after the backoff, "
                "then a 0.05 m alignment/approach correction if needed. Otherwise gain clearance "
                f"with move({retreat}) before choosing a clear route. Do not repeat the larger "
                "collided drive.")
    if last["tool"] == "pickup" and last["result"].get("success"):
        return " Pickup succeeded. Now find the requested drop zone in the camera; do not search for the object again."
    if last["tool"] == "pickup" and not last["result"].get("success"):
        return (" Pickup failed: the target was not within reach/alignment at that pose. "
                "Do not repeat pickup. Use a small turn if off-center, otherwise approach "
                "0.2 m along clear floor while the whole target is visible, or 0.1 m when "
                "its lower part is first clipped; near reach use 0.05 m to avoid overshooting. "
                "Reassess before the next pickup trial.")
    return ""


def _pose(state):
    return state["x"], state["y"], state["heading"]


def _pose_changed(before, after):
    """Ignore numeric pose jitter; an ineffective/zero command is no correction."""
    return (math.hypot(before[0] - after[0], before[1] - after[1]) > 0.001
            or abs(math.remainder(before[2] - after[2], 360)) > 0.1)


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
    failed_manipulations = {}
    visual_memory = ""
    blocked_carry_heading = None
    decision_report = {}

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
                    "result": result, "frame_path": frame_name, **decision_report,
                }) + "\n")
                journal.flush()

            for step in range(1, max_decisions + 1):
                decision_report = {}
                depth_reader = getattr(engine, "depth", None)
                depth = depth_reader() if callable(depth_reader) else None
                depth = depth if finite_number(depth) and 0 <= depth <= 10 else None
                max_forward = max(0.0, depth - 0.04) if depth is not None else None
                context = json.dumps({
                    "command": command,
                    "state": state,
                    "remaining_decisions": max_decisions - step + 1,
                    "recent_actions": list(history),
                    "previous_visual_observation": visual_memory,
                    **({"depth": depth, "max_forward_distance": max_forward} if depth is not None else {}),
                }, separators=(",", ":"))
                decisions = step
                system = _navigation_prompt(history)
                alternating_turns = system != SYSTEM_PROMPT
                system += (" Your current objective is delivery: holding is true; locate and approach "
                           "the requested zone, not the picked object." if state["holding"] else
                           " Your current objective is acquisition: holding is false; align, approach "
                           "and pick up the requested object before navigating to the zone.")
                system += _recovery_prompt(history)
                if blocked_carry_heading is not None:
                    if abs(math.remainder(state['heading'] - blocked_carry_heading, 360)) >= 15:
                        blocked_carry_heading = None
                    else:
                        system += (" This carrying heading led to contact. Reversing alone does not clear "
                                   "the blocked route. Turn at least 15 degrees toward visible clear floor "
                                   "before another forward drive.")
                if max_forward is not None:
                    system += (f" Current forward surface clearance is {depth:.3f} m. "
                               f"Do not request a positive move beyond {max_forward:.3f} m. "
                               "If the requested object is centered and close, pick up now rather than bumping it.")
                failed_manipulations = {
                    name: pose for name, pose in failed_manipulations.items()
                    if not _pose_changed(pose, _pose(state))
                }
                disabled = list(failed_manipulations)
                if disabled:
                    system += (" Actions currently unavailable at this unchanged pose: " + ", ".join(disabled)
                               + ". Their previous attempts failed here. Choose a meaningful move or turn "
                               "to correct position/alignment before trying them again. A zero command "
                               "or another failed manipulation does not change this restriction.")
                if rejected_turns:
                    system += (
                        " The previous action proposal was rejected before dispatch. "
                        "The rover has not moved. Choose a different action using the same image."
                    )
                try:
                    set_available = getattr(claude, "set_available_actions", None)
                    if callable(set_available):
                        set_available([name for name in ("move", "turn", "pickup", "drop")
                                       if name not in disabled])
                    decision = claude.decide(
                        [{"role": "user", "content": [{"type": "text", "text": context}, frame]}],
                        system=system,
                    )
                except (ClaudeError, OpenAIError):
                    log_decision(step, datetime.now(timezone.utc).isoformat(), None, {},
                                 {"outcome": "not_dispatched", "reason": "provider_error"}, frame_name)
                    raise
                if decision.observation:
                    visual_memory = decision.observation
                for key, count in decision.usage.items():
                    usage[key] = usage.get(key, 0) + count

                proposed = decision
                decision, correction = correct_action(decision, holding=state['holding'],
                                                       depth=depth, disabled=disabled, history=history,
                                                       allow_exploration=blocked_carry_heading is None,
                                                       recovering=state['state'] == 'Blocked')
                if proposed.target_view is not None:
                    decision_report = {'proposal': {'tool': proposed.name, 'args': proposed.args},
                                       'target_view': proposed.target_view,
                                       'visual_observation': proposed.observation,
                                       'controller_correction': correction}
                timestamp = datetime.now(timezone.utc).isoformat()
                repeats_collision = (
                    decision.name == "move" and history and history[-1]["tool"] == "move"
                    and history[-1]["result"].get("collided")
                    and decision.args["distance"] * history[-1]["args"]["distance"] > 0
                    and abs(decision.args["distance"]) > 0.05
                )
                unsafe_forward = (decision.name == "move" and max_forward is not None
                                  and decision.args["distance"] > max_forward)
                blocked_carry = (blocked_carry_heading is not None and decision.name == 'move'
                                 and decision.args['distance'] > 0)
                repeats_failed_turn = (decision.name == 'turn' and history
                    and history[-1]['tool'] == 'turn' and not history[-1]['result']['success']
                    and decision.args['degrees'] * history[-1]['args']['degrees'] > 0)
                if repeats_failed_turn or blocked_carry or unsafe_forward or decision.name in disabled or repeats_collision or (decision.name == "turn" and (
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
                if decision.name in ("pickup", "drop") and (
                    not result["success"] or result.get("in_zone") is False
                ):
                    failed_manipulations[decision.name] = _pose(state)
                if decision.name == "pickup" and result["success"]:
                    visual_memory = "Pickup confirmed by action result. Track the requested drop zone now."
                if state['holding'] and decision.name == 'move' and result.get('collided'):
                    blocked_carry_heading = state['heading']
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
