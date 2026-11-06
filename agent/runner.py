"""Run one operator-started rover command."""

import base64
import json
import math
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import mkdtemp

from PIL import Image

from .claude import ClaudeClient, ClaudeError, Decision
from .engine import ActionOutcomeUncertain, EngineClient, EngineError
from .openai_client import OpenAIClient, OpenAIError
from .navigation import MAX_PICKUP_PROBE, NavigationMemory, REJECTION_GUIDANCE
from .observations import VisualMemory
from .tracking import ApproachTracker
from .approach import FloorApproach
from .delivery import GreenDropZone
from .routing import CameraRoute


MAX_DECISIONS = 60
MAX_REJECTED_PROPOSALS = 3
MAX_TURNS_WITHOUT_PROGRESS = 12
MAX_LOCAL_APPROACH_PROBES = 12
RECOVERY_TOOLS = {
    "turn_loop": ("move",),
    "recovery_requires_translation": ("move", "turn"),
    "pickup_base_still_visible": ("move",),
    "near_target_step_too_large": ("move",),
    "previously_blocked_approach": ("move", "turn"),
    "pickup_check_required": ("pickup",),
    "drop_outside_observed_zone": ("move", "turn"),
}
SYSTEM_PROMPT = (
    "Control the rover to complete the operator's fetch command. Choose one action per decision. "
    "Use front camera observations as your only source for object and zone locations. "
    "Remember earlier camera observations with their rover poses, while using the latest "
    "image to verify the current approach. Never assume a remembered object is still there. "
    "Camera calibration: the camera is 0.22 metres ahead of the rover center, 0.5 metres "
    "above the floor, tilted down 10 degrees, with 90-degree horizontal field of view. "
    "Pickup reach is measured from the rover center, not from the camera. "
    "The rover's collision footprint is 0.52 metres long and 0.46 metres wide; "
    "leave clearance for the whole body when choosing a route. "
    "Match both color and silhouette: spheres look round, cubes have corners, cylinders "
    "have upright sides, and cones taper to a tip. "
    "In this 3D task, square or block means a cube; use the requested color when given. "
    "Approach a cube toward a broad face, not directly toward a corner. If two similarly "
    "wide faces meet in the center of its silhouette, choose a clear side approach while "
    "still at a safe distance; its corner can contact the body before pickup is in reach. "
    "Aim toward the target: image left means a negative turn and image right means a positive turn. "
    "A target center in the middle third of the image is aligned enough for travel and "
    "pickup: grasp tolerance is 30 degrees. Do not spend decisions centering it exactly. "
    "When its base is clipped and it is in the middle third, try pickup before another "
    "alignment turn or forward drive. Use useful turns greater than 2 degrees, "
    "since smaller turns do not move this rover. Advance about 0.3 to 0.5 metres along "
    "clear floor and reassess. If a turn moved the target farther from center, correct once "
    "in the opposite direction, then move toward it when sufficiently aligned. "
    "Avoid long blind drives. When the target is close, use at most 0.02 metre moves. "
    "All visible objects, including non-target objects, are obstacles. A clear image center "
    "does not guarantee wheel clearance: a large object low on either side can still block "
    "the rover body. Detour around foreground obstacles rather than steering directly at "
    "a farther target through them. "
    "Pick up only when the requested object is centered and within 0.5 metres. "
    "If pickup fails, use the frame to adjust distance or alignment before trying again. "
    "A target clipped at the image bottom can still be ahead of the rover and out of reach; "
    "image clipping alone is not a reason to reverse. After a failed pickup, advance "
    "only 0.02 metres on a clear approach, then check pickup again before any move or turn, "
    "so you do not pass the narrow pickup window or rotate into the nearby object. "
    "If the target is out of view, scan consistently in one direction in 45 degree turns. "
    "If several turns have not helped, change viewpoint by moving a short distance into clear floor. "
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
    model_requests: int = 0
    local_pickup_checks: bool = False


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


def _alternating_turns(history):
    turns = []
    for action in reversed(history):
        if action["tool"] != "turn" or not action["result"]["success"]:
            break
        turns.append(action["args"]["degrees"])
        if len(turns) == 3:
            break
    return len(turns) == 3 and turns[0] * turns[1] < 0 and turns[1] * turns[2] < 0


def _navigation_prompt(history):
    prompt = SYSTEM_PROMPT
    if _alternating_turns(history):
        prompt += (
            " Your last three successful turns alternated direction without translating. "
            "Stop fine alignment. If the target is close enough and aligned, pick up now. "
            "If the current image shows clear floor toward the target, move forward 0.2 metres "
            "and reassess. If the path is blocked, use the collision recovery instructions. "
            "If the target is out of view and a move is unsafe, scan in the same direction "
            "as the last turn. Do not immediately reverse the last turn again."
        )
    collisions = sum(action["result"].get("collided", False) for action in history)
    if collisions >= 2:
        prompt += (
            " Multiple recent moves collided. A successful reverse only creates clearance; "
            "it does not make the original approach safe. Do not drive back into that corridor. "
            "After retreating safely, inspect the refreshed image, turn toward a clear side "
            "route far enough to clear the foreground obstacle with the whole rover, then "
            "advance at most 0.2 metres and reassess. Keep seeking the object in the original "
            "command. Do not pick up a different object merely because it blocks the route."
        )
    return prompt


def run_command(
    command, *, engine=None, claude=None, output_root="runs",
    max_decisions=MAX_DECISIONS, audit_requests=False, provider="claude",
    local_pickup_checks=False,
):
    """Return why the agent stopped. The operator owns evaluation and lifecycle."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Provide a fetch command.")
    if type(max_decisions) is not int or not 1 <= max_decisions <= MAX_DECISIONS:
        raise ValueError("Decision limit must be between 1 and 60.")
    if type(local_pickup_checks) is not bool:
        raise ValueError("Local pickup checks must be enabled or disabled.")
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

    factory_client = claude is None
    if factory_client:
        claude = clients[provider](audit_request=audit_request if audit_requests else None)
    history = deque(maxlen=8)
    usage = {"input_tokens": 0, "output_tokens": 0}
    decisions = 0
    model_requests = 0
    status = "decision_limit"
    rejected_proposals = 0
    turns_without_progress = 0
    navigation = NavigationMemory()
    visual_memory = VisualMemory()
    last_rejection = None
    tracker = ApproachTracker() if local_pickup_checks else None
    cone_approach = bool(re.search(r"\bcone\b", command, re.I))
    floor_approach = (FloorApproach(max_heading_change=15.0 if cone_approach else 3.0)
                      if local_pickup_checks else None)
    delivery = GreenDropZone(command) if local_pickup_checks else None
    routing = CameraRoute(command) if local_pickup_checks else None
    local_approach_probes = 0

    def reset_tracking(*, keep_range=False):
        nonlocal local_approach_probes
        if tracker is not None:
            tracker.reset()
        if floor_approach is not None and not keep_range:
            floor_approach.reset()
        local_approach_probes = 0

    try:
        with (run_dir / "steps.jsonl").open("x", encoding="utf-8") as journal, \
             (run_dir / "decision_sources.jsonl").open("x", encoding="utf-8") as sources:
            state = engine.state()
            terminal = _terminal_status(state)
            if terminal:
                return RunResult(terminal, decisions, usage, run_dir,
                                 model_requests, local_pickup_checks)
            frame_name, frame = _capture(engine, run_dir, 0)
            navigation.record_view(state)

            def log_decision(step, timestamp, tool, args, result, frame_name):
                journal.write(json.dumps({
                    "step": step, "timestamp": timestamp, "tool": tool, "args": args,
                    "result": result, "frame_path": frame_name,
                }) + "\n")
                journal.flush()

            def adjusted(proposal, replacement, reason, step, frame_name):
                name, args = replacement
                with (run_dir / "adjustments.jsonl").open("a", encoding="utf-8") as adjustments:
                    adjustments.write(json.dumps({
                        "step": step, "reason": reason,
                        "proposed_tool": proposal.name, "proposed_args": proposal.args,
                        "applied_tool": name, "applied_args": args, "frame_path": frame_name,
                    }) + "\n")
                return Decision(name, args, proposal.usage, proposal.observation)

            for step in range(1, max_decisions + 1):
                if routing is not None:
                    routing.observe(run_dir / frame_name, state)
                if delivery is not None:
                    delivery.observe(run_dir / frame_name, state)
                context = json.dumps({
                    "command": command,
                    "state": state,
                    "remaining_decisions": max_decisions - step + 1,
                    "recent_actions": list(history),
                }, separators=(",", ":"))
                decisions = step
                system = _navigation_prompt(history) + navigation.guidance() + visual_memory.guidance()
                system += navigation.recovery_guidance(state)
                if routing is not None:
                    system += routing.guidance(state)
                if delivery is not None:
                    system += delivery.guidance(state)
                alternating_turns = _alternating_turns(history)
                if rejected_proposals:
                    system += (
                        " The previous action proposal was rejected before dispatch. "
                        "The rover has not moved. Choose a different action using the same image."
                    )
                    system += " " + REJECTION_GUIDANCE[last_rejection]
                local_check = (
                    local_pickup_checks and navigation.probe_check_required
                    and navigation.approaching(state)
                    and state["state"] != "Blocked" and not state["holding"]
                )
                local_probe = False
                probe_distance = MAX_PICKUP_PROBE
                if not local_check and tracker is not None and tracker.active:
                    if (local_approach_probes >= MAX_LOCAL_APPROACH_PROBES
                            or state["state"] == "Blocked" or state["holding"]
                            or not navigation.approaching(state)):
                        reset_tracking()
                    elif (history and history[-1]["tool"] == "pickup"
                          and not history[-1]["result"]["success"]):
                        if tracker.update(run_dir / frame_name):
                            measured_step = floor_approach.distance(run_dir / frame_name, state)
                            if measured_step == 0:
                                reset_tracking()
                            else:
                                probe_distance = (MAX_PICKUP_PROBE if measured_step is None
                                                  else measured_step)
                                local_probe = True
                        else:
                            reset_tracking()
                source = ("local_pickup_check" if local_check else
                          "local_approach_probe" if local_probe else "model")
                if source == "model":
                    model_requests += 1
                sources.write(json.dumps({
                    "step": step,
                    "source": source,
                    "request_index": model_requests if source == "model" else None,
                }) + "\n")
                sources.flush()
                if local_check:
                    # The preceding model-directed probe requires a feedback
                    # check before any further motion. This is one bounded
                    # decision with no new model request or scene information.
                    decision = Decision("pickup", {}, {})
                elif local_probe:
                    decision = Decision("move", {"distance": probe_distance}, {})
                else:
                    try:
                        options = {"system": system}
                        allowed_tools = RECOVERY_TOOLS.get(last_rejection) if rejected_proposals else None
                        if factory_client and allowed_tools is not None:
                            options["allowed_tools"] = allowed_tools
                        decision = claude.decide(
                            [{"role": "user", "content": [{"type": "text", "text": context}, frame]}],
                            **options,
                        )
                    except (ClaudeError, OpenAIError):
                        log_decision(step, datetime.now(timezone.utc).isoformat(), None, {},
                                     {"outcome": "not_dispatched", "reason": "provider_error"}, frame_name)
                        raise
                for key, count in decision.usage.items():
                    usage[key] = usage.get(key, 0) + count
                navigation.set_observation(decision.observation)
                observation = visual_memory.remember(decision.observation, state, frame_name, step)
                if observation is not None:
                    with (run_dir / "observations.jsonl").open("a", encoding="utf-8") as observations:
                        observations.write(json.dumps(observation) + "\n")

                if (routing is not None and cone_approach and step == 1
                        and navigation.target_base == "unknown" and not state["holding"]
                        and decision.name == "turn"):
                    search = routing.search_turn(state)
                    if search is not None and search * decision.args["degrees"] < 0:
                        decision = adjusted(decision, ("turn", {"degrees": search}),
                                            "camera_occlusion_search", step, frame_name)
                early_drop = (delivery is not None and delivery.known
                              and state["holding"] and state["state"] != "Blocked"
                              and decision.name == "drop" and not delivery.ready(state))
                if early_drop:
                    correction = delivery.correction(state)
                    if correction is not None:
                        decision = adjusted(decision, correction, "drop_outside_observed_zone",
                                            step, frame_name)
                        early_drop = False
                if routing is not None and source == "model":
                    correction = routing.propose(
                        decision.name, decision.args, state,
                        preferred_side=navigation.detour_turn_sign if navigation.repositioning else None,
                    )
                    if correction is not None:
                        decision = adjusted(decision, correction, "camera_obstacle_route", step, frame_name)
                timestamp = datetime.now(timezone.utc).isoformat()
                rejection = navigation.rejection(
                    decision.name, decision.args, state, frame_name,
                    probe_limit=probe_distance if local_probe else MAX_PICKUP_PROBE,
                )
                if (routing is not None and source == "model"
                        and rejection in ("previously_blocked_approach", "recovery_requires_translation")):
                    escape = navigation.escape_turn(state)
                    if escape is not None:
                        decision = adjusted(decision, ("turn", {"degrees": escape}),
                                            "recovery_escape_scan", step, frame_name)
                        rejection = navigation.rejection(decision.name, decision.args, state, frame_name)
                if early_drop:
                    rejection = "drop_outside_observed_zone"
                if decision.name == "turn" and (
                    turns_without_progress >= MAX_TURNS_WITHOUT_PROGRESS
                    or (alternating_turns
                        and decision.args["degrees"] * history[-1]["args"]["degrees"] < 0)
                ):
                    rejection = "turn_loop"
                if rejection:
                    if local_probe:
                        reset_tracking()
                    log_decision(step, timestamp, decision.name, decision.args,
                                 {"outcome": "not_dispatched", "reason": "navigation_guard"}, frame_name)
                    with (run_dir / "navigation.jsonl").open("a", encoding="utf-8") as events:
                        events.write(json.dumps({"step": step, "reason": rejection,
                                                 "frame_path": frame_name}) + "\n")
                    last_rejection = rejection
                    rejected_proposals += 1
                    if rejected_proposals >= MAX_REJECTED_PROPOSALS:
                        status = "navigation_stalled"
                        break
                    continue

                rejected_proposals = 0
                action_frame_name = frame_name
                if (floor_approach is not None and source == "model"
                        and decision.name == "move" and decision.args["distance"] > 0
                        and not state["holding"] and not navigation.repositioning):
                    floor_approach.observe(run_dir / frame_name, state, navigation.target_base)
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
                next_state = engine.state()
                pickup_base = navigation.target_base
                navigation.observe(decision.name, decision.args, result, state, next_state,
                                   action_frame_name)
                if routing is not None and (
                    result.get("collided") or (decision.name == "turn" and not result["success"])
                ):
                    routing.contact()
                if tracker is not None:
                    if (decision.name == "turn"
                            or (decision.name == "move" and (not result["success"] or result.get("collided")))
                            or (decision.name == "pickup" and result["success"])):
                        keep_range = (cone_approach and decision.name == "turn" and result["success"]
                                      and not navigation.repositioning and not next_state["holding"]
                                      and next_state["state"] != "Blocked"
                                      and abs(math.remainder(next_state["heading"] - state["heading"], 360)) <= 15)
                        reset_tracking(keep_range=keep_range)
                        if (keep_range and not floor_approach.reorient(run_dir / frame_name, next_state)):
                            floor_approach.reset()
                    elif (source == "model" and decision.name == "pickup" and not result["success"]
                          and pickup_base == "clipped" and not navigation.repositioning
                          and not next_state["holding"]
                          and next_state["state"] != "Blocked"):
                        reset_tracking(keep_range=True)
                        if not tracker.start(run_dir / action_frame_name):
                            reset_tracking()
                    if local_probe and result["success"] and not result.get("collided"):
                        local_approach_probes += 1
                state = next_state
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
    return RunResult(status, decisions, usage, run_dir, model_requests, local_pickup_checks)
