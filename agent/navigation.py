"""Provider-independent motion guards using only public rover feedback."""

import math
from collections import OrderedDict, deque
from .observations import target_base_status

MAX_PICKUP_PROBE = 0.02


class NavigationMemory:
    """Remember unsafe approaches within one command, without object metadata."""

    def __init__(self):
        self.collisions = deque(maxlen=4)
        self.failed_pickup_frame = None
        self.views = OrderedDict()
        self.current_cell = None
        self.repositioning = False
        self.detour_turn_sign = None
        self.detour_origin = None
        self.detour_direction = None
        self.detour_progress = 0.0
        self.failed_pickup_pose = None
        self.probe_check_required = False
        self.target_base = "unknown"

    def set_observation(self, note):
        self.target_base = target_base_status(note)

    def approaching(self, state):
        if (self.failed_pickup_pose is None or self.repositioning or state["holding"]
                or self.target_base == "visible"):
            return False
        pose = self.failed_pickup_pose
        return (math.hypot(state["x"] - pose["x"], state["y"] - pose["y"]) <= 0.5
                and abs(math.remainder(state["heading"] - pose["heading"], 360)) <= 30)

    @staticmethod
    def lateral_offset(state, origin, direction):
        angle = math.radians(direction)
        return abs(math.cos(angle) * (state["y"] - origin[1])
                   + math.sin(angle) * (state["x"] - origin[0]))

    def record_view(self, state):
        cell = (round(state["x"] / 0.75), round(state["y"] / 0.75))
        sector = round(state["heading"] / 45) % 8
        self.views.setdefault(cell, set()).add(sector)
        self.views.move_to_end(cell)
        while len(self.views) > 16:
            self.views.popitem(last=False)
        self.current_cell = cell

    def escape_turn(self, state):
        """Suggest a bounded continued scan after the camera chose a side.

        This does not select a side, authorize a drive, or retire a collision
        ban. It gives the caller a concrete alternative when a proposed drive
        still points into the blocked fan, or a proposed turn undoes the detour.
        The caller must retain camera checks, normal guards, and a fresh look.
        """
        if (not self.repositioning or self.detour_turn_sign is None
                or not self.collisions):
            return None
        heading = state.get("heading")
        if type(heading) not in (int, float) or not math.isfinite(heading):
            return None
        direction = self.collisions[-1]["direction"]
        difference = math.remainder(heading - direction, 360)
        if abs(difference) >= 55:
            return None
        progress = max(0.0, self.detour_turn_sign * difference)
        return self.detour_turn_sign * min(25.0, max(3.0, 55.0 - progress))

    def recovery_guidance(self, state):
        """Explain the measured escape angle without supplying scene facts."""
        if not self.repositioning or not self.collisions:
            return ""
        turn = self.escape_turn(state)
        if self.detour_turn_sign is None:
            return (
                " Collision recovery has no chosen side yet. Retreat away from the contact "
                "if the current camera supports it, then choose a visibly clear side and "
                "make a useful turn. Small heading corrections do not clear the blocked corridor."
            )
        if turn is not None:
            return (
                " The chosen detour still points too close to the collided approach. "
                f"Continue the same-side scan by {turn:+.1f} degrees now, then inspect the fresh image. "
                "Aim for at least 55 degrees of heading separation before side-route travel; "
                "do not undo the turn or drive back into the remembered blocked corridor. "
                "A safe reverse retreat remains available when it leads away from contact."
            )
        return (
            " The detour heading has separated from the collided approach. Use the fresh "
            "camera to choose a clear short side-route move, or continue scanning in the "
            "chosen direction if the floor is blocked. "
            f"Measured lateral clearance is {self.detour_progress:.2f} m; "
            "build more than 0.30 m before turning back toward the target. "
            "Heading separation alone does not prove the floor is clear."
        )

    def rejection(self, name, args, state, frame, *, probe_limit=MAX_PICKUP_PROBE):
        if name == "pickup" and state["holding"]:
            return "already_holding"
        if name == "pickup" and self.target_base == "visible":
            return "pickup_base_still_visible"
        if name == "drop" and not state["holding"]:
            return "nothing_to_drop"
        if name in ("move", "turn") and self.probe_check_required and self.approaching(state):
            return "pickup_check_required"
        if name == "turn" and abs(args["degrees"]) <= 2:
            return "turn_requires_useful_angle"
        if (name == "turn" and self.repositioning and self.detour_turn_sign is not None
                and args["degrees"] * self.detour_turn_sign < 0):
            return "recovery_requires_translation"
        if name == "pickup" and self.failed_pickup_pose is not None:
            pose = self.failed_pickup_pose
            moved = math.hypot(state["x"] - pose["x"], state["y"] - pose["y"])
            rotated = abs(math.remainder(state["heading"] - pose["heading"], 360))
            if frame == self.failed_pickup_frame or (moved <= 0.005 and rotated <= 0.5):
                return "pickup_requires_new_viewpoint"
        if name != "move":
            return None
        distance = args["distance"]
        if distance == 0:
            return "move_requires_progress"
        if distance > 0 and self.approaching(state):
            if distance > probe_limit:
                return "near_target_step_too_large"
        direction = state["heading"] + (180 if distance < 0 else 0)
        angle_radians = math.radians(direction)
        ux, uy = math.cos(angle_radians), -math.sin(angle_radians)
        for blocked in self.collisions:
            if not blocked.get("active", True):
                continue
            separation = math.hypot(state["x"] - blocked["x"], state["y"] - blocked["y"])
            angle = abs(math.remainder(direction - blocked["direction"], 360))
            lateral = self.lateral_offset(state, (blocked["x"], blocked["y"]), blocked["direction"])
            along = max(0, min(abs(distance),
                              (blocked["x"] - state["x"]) * ux + (blocked["y"] - state["y"]) * uy))
            swept_distance = math.hypot(state["x"] + along * ux - blocked["x"],
                                        state["y"] + along * uy - blocked["y"])
            if angle <= 25 and ((separation <= 0.8 and lateral <= 0.3) or swept_distance <= 0.3):
                return "previously_blocked_approach"
        return None

    def observe(self, name, args, result, before, after, frame):
        if name == "move" and args["distance"] > 0 and result["success"] and self.approaching(before):
            self.probe_check_required = True
        if name in ("move", "turn"):
            self.record_view(after)
        if name == "pickup":
            self.failed_pickup_frame = frame if not result["success"] else None
            self.failed_pickup_pose = dict(after) if not result["success"] else None
            self.probe_check_required = False
            if result["success"]:
                self.repositioning = False
                self.detour_turn_sign = None
                self.detour_origin = None
                self.detour_direction = None
                self.detour_progress = 0.0
        if name == "turn" and result["success"] and self.repositioning:
            actual_turn = math.remainder(after["heading"] - before["heading"], 360)
            if self.detour_turn_sign is None and abs(actual_turn) > 2:
                self.detour_turn_sign = math.copysign(1, actual_turn)
                self.detour_origin = (after["x"], after["y"])
                self.detour_direction = self.collisions[-1]["direction"]
        if name == "move" and result["success"] and self.detour_origin is not None:
            self.detour_progress = self.lateral_offset(after, self.detour_origin, self.detour_direction)
            if self.detour_progress > 0.3:
                # A translated side route establishes a new approach. Keeping a
                # whole neighbourhood forbidden would prevent legitimate pickup
                # probes after going around an obstacle. Retain the evidence,
                # but retire nearby directional bans; distant blocks still apply.
                latest = self.collisions[-1]
                for blocked in self.collisions:
                    same_direction = abs(math.remainder(blocked["direction"] - latest["direction"], 360)) <= 25
                    if same_direction and math.hypot(blocked["x"] - latest["x"], blocked["y"] - latest["y"]) <= 0.8:
                        blocked["active"] = False
                self.repositioning = False
                self.detour_turn_sign = None
                self.detour_origin = None
                self.detour_direction = None
                self.detour_progress = 0.0
        if name == "move" and result.get("collided"):
            self.failed_pickup_pose = None
            self.probe_check_required = False
            self.repositioning = True
            self.detour_turn_sign = None
            self.detour_origin = None
            self.detour_direction = None
            self.detour_progress = 0.0
            self.collisions.append({
                "x": after["x"], "y": after["y"],
                "direction": before["heading"] + (180 if args["distance"] < 0 else 0),
                "active": True,
            })

    def guidance(self):
        prompt = ""
        if self.target_base == "visible":
            prompt += (
                " The target's actual floor contact is visible in the current camera. "
                "This camera cannot see floor closer than about 0.69 metres from the rover "
                "center, so the target is still outside 0.5 metre pickup reach. Approach "
                "along clear floor in 0.1 to 0.3 metre steps and reassess, rather than "
                "starting tiny pickup probes while still far away."
            )
        if self.repositioning and self.detour_turn_sign is not None:
            prompt += (
                " Recovery phase: you turned toward a side route but have not translated "
                "sideways far enough to leave the failed approach. Before turning back toward the "
                "target, build more than 0.3 metres of lateral clearance using short moves "
                "along visibly clear floor in this new direction. "
                f"Measured lateral progress is {self.detour_progress:.2f} metres. "
                "If this view is blocked, continue scanning in the chosen turn "
                "direction. Do not undo the turn while still in the same place."
            )
        if self.failed_pickup_frame is not None and self.target_base != "visible":
            prompt += (
                " The last pickup failed. A large image does not prove pickup reach. "
                "If the target is aligned and floor ahead is clear, cautiously reduce the "
                "distance by at most 0.02 metres, then check pickup before advancing again. The pickup radius is from "
                "the rover center, which is behind the camera. Do not reverse solely because the "
                "target looks large; retreat when contact or the visible geometry requires it."
            )
        if self.current_cell is not None and len(self.views[self.current_cell]) >= 7:
            prompt += (
                " Search memory: this area has already been viewed in nearly every direction. "
                "Repeating the scan here adds little information. Choose a safe short move "
                "toward clear floor in a different area, then search from that new viewpoint."
            )
        if not self.collisions:
            return prompt
        return prompt + (
            " Motion safety memory remembers recent collided approach directions even when "
            "they leave the short action history. Reverse away if safe, then choose a visibly "
            "clear side route with substantial heading change before approaching again. "
            "Small alternating turns do not establish a detour."
        )


REJECTION_GUIDANCE = {
    "drop_outside_observed_zone": "The held object's release point is outside the floor zone observed by the camera. Keep holding it, align with the observed zone interior, and approach before dropping. Follow the current camera and collision guards.",
    "pickup_base_still_visible": "The visible floor contact means the target is still beyond pickup reach for this calibrated camera. Advance cautiously along clear floor and reassess. Use tiny pickup probes after its base becomes clipped, not while still far away.",
    "already_holding": "The rover is already holding an object. Locate the drop zone and deliver it.",
    "nothing_to_drop": "Nothing is held. Find and pick up the requested target before dropping.",
    "turn_requires_useful_angle": "Turns of 2 degrees or less do not move this rover. Choose a useful turn greater than 2 degrees, or pick up if already aligned.",
    "near_target_step_too_large": "Near-target probing is active after a failed pickup. Use at most 0.02 metres forward, then check pickup before another forward move.",
    "pickup_check_required": "The last forward probe may have entered pickup reach. Check pickup now before any other motion. Turning near the target can cause contact, and moving again can overshoot reach. If pickup fails, then reassess alignment and clearance.",
    "recovery_requires_translation": (
        "Turning back now would cancel the detour without changing position. First take "
        "a safe short move along the clear side route, or continue scanning in the same "
        "turn direction if no safe move is visible. The original target remains the task."
    ),
    "pickup_requires_new_viewpoint": (
        "Pickup failed with this exact image. Another pickup without moving cannot resolve "
        "distance or alignment. If the target is centered and the approach is clear, try a "
        "0.02 metre forward step and reassess; if misaligned, turn to align. Do not assume "
        "that a large image of an object proves it is within pickup reach."
    ),
    "move_requires_progress": "Use a short nonzero move along visibly clear floor and reassess.",
    "previously_blocked_approach": (
        "This motion would re-enter a recently collided approach near the same place. "
        "Choose a safe retreat or turn toward a clear side route; do not retry that motion."
    ),
    "turn_loop": "Choose progress using the latest image instead of repeating the turn loop.",
}
