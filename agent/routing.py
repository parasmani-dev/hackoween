"""Camera-derived panel clearance and a short, latched detour.

Only neutral/cool tall surfaces with a visible floor boundary are mapped. The
map contains observed ground points and public rover poses, never scene data.
"""

import math
import re
from collections import OrderedDict, deque

from PIL import Image

from .tracking import ApproachTracker, _hue_distance


class CameraRoute:
    width, height = 160, 120

    def __init__(self, command=""):
        self._points = OrderedDict()
        self._edges = set()
        self._poses = deque(maxlen=12)
        self._origin = None
        self._travel_heading = None
        self._sign = None
        self._carrying = False
        self._last_observed_pose = None
        self._blocked_heading = None
        self._vision = ApproachTracker()
        self._objects = []
        self._object_serial = 0
        hues = {"red": ((0, 15), (240, 255)), "yellow": ((22, 45),),
                "blue": ((145, 190),), "green": ((65, 116),),
                "orange": ((10, 25),), "purple": ((185, 230),),
                "pink": ((220, 245),)}
        match = re.search(r"\b(red|yellow|blue|green|orange|purple|pink)\s+"
                          r"(?:sphere|cube|square|cone|cylinder|block|ball|object)\b",
                          command if isinstance(command, str) else "", re.I)
        self._target_hues = hues[match[1].lower()] if match else ()

    def contact(self):
        """Release the local detour after contact while retaining its evidence."""
        self._origin = self._travel_heading = self._sign = None
        self._blocked_heading = self._last_observed_pose[2] if self._last_observed_pose else None

    @staticmethod
    def _pose(state):
        try:
            values = tuple(state[key] for key in ("x", "y", "heading"))
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
                return None
            return tuple(float(value) for value in values)
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _local(point, pose):
        angle = math.radians(pose[2])
        dx, dy = point[0] - pose[0], point[1] - pose[1]
        return (dx * math.cos(angle) - dy * math.sin(angle),
                -dx * math.sin(angle) - dy * math.cos(angle))

    @staticmethod
    def _world(forward, right, pose):
        angle = math.radians(pose[2])
        return (pose[0] + forward * math.cos(angle) - right * math.sin(angle),
                pose[1] - forward * math.sin(angle) - right * math.cos(angle))

    def _floor_lines(self, path):
        try:
            with Image.open(path) as image:
                rgb = image.convert("RGB").resize((160, 120), Image.Resampling.NEAREST)
                pixels, hsv = rgb.tobytes(), rgb.convert("HSV").tobytes()
        except (OSError, ValueError, TypeError, AttributeError, Image.DecompressionBombError):
            return []
        mask, wall_pixels = bytearray(), bytearray()
        for offset in range(0, len(pixels), 3):
            r, g, b = pixels[offset:offset + 3]
            hue, saturation, value = hsv[offset:offset + 3]
            neutral = value < 195 and saturation < 145 and b >= r - 4 and b >= g - 5
            wall = (115 <= hue <= 140 and saturation >= 80 and value >= 40
                    and g > r * 1.2 and b > r * 1.2
                    and 0.8 * b <= g <= 1.25 * b)
            mask.append(neutral or wall)
            wall_pixels.append(wall)
        visited = bytearray(160 * 120)
        lines = []
        for seed, enabled in enumerate(mask):
            if not enabled or visited[seed]:
                continue
            visited[seed] = 1
            queue, offset = [seed], 0
            bottom = {}
            min_y, max_y = 120, 0
            wall_count = 0
            while offset < len(queue):
                index = queue[offset]
                offset += 1
                x, y = index % 160, index // 160
                wall_count += wall_pixels[index]
                bottom[x] = max(y, bottom.get(x, -1))
                min_y, max_y = min(min_y, y), max(max_y, y)
                neighbors = []
                if x: neighbors.append(index - 1)
                if x < 159: neighbors.append(index + 1)
                if y: neighbors.append(index - 160)
                if y < 119: neighbors.append(index + 160)
                for neighbor in neighbors:
                    if mask[neighbor] and not visited[neighbor]:
                        visited[neighbor] = 1
                        queue.append(neighbor)
            wall = wall_count >= max(12, len(queue) * 0.2)
            if len(queue) < 384 or max_y - min_y < (12 if wall else 36):
                continue
            line = []
            for x in sorted(bottom)[::2]:
                y = bottom[x]
                if not 65 <= y < 117:
                    continue
                pixel_offset = ((y + 2) * 160 + x) * 3
                _, saturation, value = hsv[pixel_offset:pixel_offset + 3]
                if saturation > 80 or value < 180:
                    continue
                q, tilt = (y + 1 - 60) / 80, math.radians(10)
                down = math.sin(tilt) + q * math.cos(tilt)
                forward = 0.22 + 0.5 * (math.cos(tilt) - q * math.sin(tilt)) / down
                right = 0.5 * ((x - 80) / 80) / down
                if wall:
                    # A bright baseboard may hide the actual wall/floor joint.
                    # Pull its estimated ground boundary closer to retain
                    # clearance rather than treating the stripe as the floor.
                    forward, right = 0.22 + 0.8 * (forward - 0.22), 0.8 * right
                if 0.3 < forward < 4:
                    line.append((forward, right))
            if line:
                lines.append(line)
        return lines

    def observe(self, path, state):
        """Remember visible panel floor edges, including after they leave view."""
        pose = self._pose(state)
        if pose is None:
            return False
        self._last_observed_pose = pose
        if state.get("state") != "Blocked" and (
            not self._poses or math.dist(pose[:2], self._poses[-1][:2]) > 0.005
        ):
            self._poses.append(pose)
        for key, point in list(self._points.items()):
            if math.dist(point, pose[:2]) > 4:
                del self._points[key]
        for line in self._floor_lines(path):
            previous = None
            for forward, right in line:
                point = self._world(forward, right, pose)
                key = (round(point[0] / 0.05), round(point[1] / 0.05))
                self._points[key] = point
                self._points.move_to_end(key)
                if previous is not None and key != previous and math.dist(point, self._points[previous]) < 0.35:
                    self._edges.add(tuple(sorted((previous, key))))
                previous = key
        self._colored_obstacles(path, pose)
        # Wall observations must not evict a foreground object just because its
        # base is now hidden by the carried object. Keep separate bounded
        # budgets: 64 surface points and eight eight-point object footprints.
        surface_keys = [key for key in self._points if len(key) == 2]
        for key in surface_keys[:-64]:
            self._points.pop(key, None)
        self._edges = {edge for edge in self._edges if all(key in self._points for key in edge)}
        return bool(self._points)

    def _colored_obstacles(self, path, pose):
        """Map visible colored foreground footprints, excluding the target.

        Dimensions come from image width and the floor projection. A marked
        green floor region and the command's target color are not obstacles.
        """
        try:
            with Image.open(path) as image:
                pixels = image.convert("RGB").resize((160, 120), Image.Resampling.NEAREST).convert("HSV").tobytes()
        except (OSError, ValueError, TypeError, AttributeError, Image.DecompressionBombError):
            return
        for component in self._vision._components(path):
            left, top, right, bottom = component.box
            if (component.area < 50 or right - left < 8 or bottom - top < 8
                    or left <= 1 or right >= 159 or bottom >= 117 or bottom < 65
                    or 65 <= component.hue <= 116
                    or any(low <= component.hue <= high for low, high in self._target_hues)):
                continue
            x = (left + right) // 2
            offset = ((bottom + 2) * 160 + x) * 3
            _, saturation, value = pixels[offset:offset + 3]
            if saturation > 80 or value < 180:
                continue
            q, tilt = (bottom - 60) / 80, math.radians(10)
            down = math.sin(tilt) + q * math.cos(tilt)
            depth = 0.5 / down
            forward = 0.22 + depth * (math.cos(tilt) - q * math.sin(tilt))
            lateral = depth * ((x - 80) / 80)
            if not 0.45 <= forward <= 2.5:
                continue
            center = self._world(forward, lateral, pose)
            radius = max(0.06, min(0.45, 1.1 * (right - left) * depth / 160 + 0.03))
            group = next((item for item in self._objects
                          if _hue_distance(item["hue"], component.hue) <= 12
                          and math.dist(item["center"], center) <= 0.35), None)
            if group is None:
                self._object_serial += 1
                group = {"tag": self._object_serial, "keys": set()}
                self._objects.append(group)
            for key in group["keys"]:
                self._points.pop(key, None)
            group.update(hue=component.hue, center=center, keys=set())
            ring = []
            for index in range(8):
                angle = index * math.tau / 8
                point = (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))
                key = (round(point[0] / 0.05), round(point[1] / 0.05), group["tag"])
                self._points[key] = point
                self._points.move_to_end(key)
                group["keys"].add(key)
                ring.append(key)
            self._edges.update(tuple(sorted((ring[index], ring[(index + 1) % 8]))) for index in range(8))
        retained = [item for item in self._objects if any(key in self._points for key in item["keys"])]
        for item in retained[:-8]:
            for key in item["keys"]:
                self._points.pop(key, None)
        self._objects = retained[-8:]

    @staticmethod
    def _crosses(first, second, lower, upper):
        # Clip a segment against the swept rectangle, including endpoints.
        begin, end = 0.0, 1.0
        for index in (0, 1):
            difference = second[index] - first[index]
            if abs(difference) < 1e-12:
                if not lower[index] <= first[index] <= upper[index]:
                    return False
                continue
            a = (lower[index] - first[index]) / difference
            b = (upper[index] - first[index]) / difference
            begin, end = max(begin, min(a, b)), min(end, max(a, b))
            if begin > end:
                return False
        return True

    def _clear(self, pose, distance):
        # The held object is attached 0.60 m ahead in Rover Lab. Include a
        # conservative 0.25 m object/clearance envelope in carried travel.
        lower, upper = (-0.31, -0.30), (distance + (0.85 if self._carrying else 0.31), 0.30)
        local = {key: self._local(point, pose) for key, point in self._points.items()}
        if any(lower[0] <= f <= upper[0] and abs(r) <= 0.30 for f, r in local.values()):
            return False
        return not any(self._crosses(local[a], local[b], lower, upper) for a, b in self._edges)

    @staticmethod
    def _circle_crosses(first, second, center=(0.60, 0.0), radius=0.25):
        dx, dy = second[0] - first[0], second[1] - first[1]
        length = dx * dx + dy * dy
        fraction = (max(0.0, min(1.0, ((center[0] - first[0]) * dx
                                      + (center[1] - first[1]) * dy) / length))
                    if length else 0.0)
        closest = (first[0] + fraction * dx, first[1] + fraction * dy)
        return math.dist(closest, center) <= radius

    def _turn_clear(self, pose, degrees):
        # Sample the actual angular arc rather than excluding an entire disk;
        # an away turn may clear a panel that a turn toward it would strike.
        samples = max(1, math.ceil(abs(degrees) / 5))
        for index in range(samples + 1):
            turned = (pose[0], pose[1], pose[2] + degrees * index / samples)
            local = {key: self._local(point, turned) for key, point in self._points.items()}
            if any(abs(f) <= 0.31 and abs(r) <= 0.30 for f, r in local.values()):
                return False
            if any(self._crosses(local[a], local[b], (-0.31, -0.30), (0.31, 0.30))
                   for a, b in self._edges):
                return False
            if self._carrying:
                if any(math.hypot(f - 0.60, r) <= 0.25 for f, r in local.values()):
                    return False
                if any(self._circle_crosses(local[a], local[b]) for a, b in self._edges):
                    return False
        return True

    def _retreat(self, pose):
        # Prefer the traversed straight corridor. After a safe scan, allow only
        # a small map-checked retreat within the recently visited neighborhood.
        available = 0.0
        for previous in reversed(self._poses):
            forward, right = self._local(previous[:2], pose)
            if (abs(math.remainder(previous[2] - pose[2], 360)) <= 5
                    and abs(right) < 0.04 and forward < -0.04):
                available = min(0.15, -forward - 0.01)
                break
        if not available:
            destination = self._world(-0.10, 0.0, pose)
            if any(0.04 < math.dist(previous[:2], pose[:2]) <= 0.35
                   and math.dist(previous[:2], destination) <= 0.30 for previous in self._poses):
                available = 0.10
            else:
                return None
        # Existing close points ahead recede during a reverse. Any remembered
        # obstacle in the rear swept corridor still blocks the retreat.
        if any(-available - 0.31 <= f < 0 and abs(r) <= 0.30
               for f, r in (self._local(point, pose) for point in self._points.values())):
            return None
        local = {key: self._local(point, pose) for key, point in self._points.items()}
        if any(self._crosses(local[a], local[b], (-available - 0.31, -0.30), (0.0, 0.30))
               for a, b in self._edges):
            return None
        return "move", {"distance": -round(available, 6)}

    def _turn_or_retreat(self, pose, degrees):
        if self._turn_clear(pose, degrees):
            return "turn", {"degrees": degrees}
        retreat = self._retreat(pose)
        if retreat is not None:
            return retreat
        # A normal navigation rejection is preferable to dispatching an unsafe
        # turn or a blind reverse when no escape has been observed.
        return "move", {"distance": 0.0}

    def propose(self, name, args, state, *, preferred_side=None):
        """Replace an unsafe forward proposal with one bounded detour action."""
        self._carrying = isinstance(state, dict) and state.get("holding") is True
        pose = self._pose(state)
        if pose is None or name not in ("move", "turn"):
            return None
        forced = (preferred_side if type(preferred_side) in (int, float)
                  and preferred_side in (-1, 1) else None)
        if forced is not None and self._sign is not None and self._sign != forced:
            self._origin = self._travel_heading = self._sign = None
        if forced is not None:
            in_blocked_fan = (self._blocked_heading is not None
                              and abs(math.remainder(pose[2] - self._blocked_heading, 360)) <= 25)
            undo_turn = name == "turn" and args.get("degrees", 0) * forced < 0
            forward = name == "move" and args.get("distance", 0) > 0
            if undo_turn or (forward and in_blocked_fan):
                if not in_blocked_fan and self._clear(pose, 0.20):
                    return "move", {"distance": 0.20}
                self._origin = self._origin or pose
                self._sign, self._travel_heading = forced, pose[2] + forced * 25
                return self._turn_or_retreat(pose, forced * 25)
        if self._origin is not None:
            forward, lateral = self._local(pose[:2], self._origin)
            if abs(lateral) > 0.40 and forward > 0.30:
                self._origin = self._travel_heading = self._sign = None
            else:
                correction = math.remainder(self._travel_heading - pose[2], 360)
                if abs(correction) > 8:
                    return self._turn_or_retreat(pose, math.copysign(min(45, abs(correction)), correction))
                if self._clear(pose, 0.20):
                    return "move", {"distance": 0.20}
                self._travel_heading = pose[2] + self._sign * 25
                return self._turn_or_retreat(pose, self._sign * 25)
        if name == "turn":
            degrees = args.get("degrees", 0)
            if not self._turn_clear(pose, degrees):
                # A blocked scan in one direction need not trap the rover in
                # place. Take an observed-clear opposite arc and commit to
                # translating along that side route before scanning back.
                sign = -1 if degrees > 0 else 1
                for magnitude in (min(45, abs(degrees)), 25):
                    alternative = (forced if forced is not None else sign) * magnitude
                    if magnitude > 2 and self._turn_clear(pose, alternative):
                        self._origin, self._sign = pose, forced if forced is not None else sign
                        self._travel_heading = pose[2] + alternative
                        return "turn", {"degrees": alternative}
                return self._retreat(pose) or ("move", {"distance": 0.0})
            return None
        if name != "move" or args.get("distance", 0) <= 0 or self._clear(pose, args["distance"]):
            return None
        candidates = []
        ahead = [(f, r) for f, r in (self._local(point, pose) for point in self._points.values()) if f > -0.1]
        side = sum(math.copysign(1, r) / max(0.1, f) for f, r in ahead)
        preferred = -1 if side >= 0 else 1
        for sign in ((forced,) if forced is not None else (preferred, -preferred)):
            turned = (pose[0], pose[1], pose[2] + sign * 45)
            candidates.append((not self._clear(turned, 0.20), sign != preferred, sign))
        _, _, self._sign = min(candidates)
        self._origin, self._travel_heading = pose, pose[2] + self._sign * 45
        return self._turn_or_retreat(pose, self._sign * 45)

    def search_turn(self, state):
        """Inspect the nearest observed occluding edge before a room-wide scan."""
        pose = self._pose(state)
        if pose is None:
            return None
        visible = [(f, r) for f, r in (self._local(point, pose) for point in self._points.values())
                   if 0.35 < f < 2.0 and 0.10 < abs(r) < 2.0]
        if not visible:
            return None
        _, right = min(visible, key=lambda point: math.hypot(*point))
        return math.copysign(45.0, right)

    def guidance(self, state):
        pose = self._pose(state)
        if pose is None or not self._points:
            return ""
        if self._origin is None:
            return (" Camera route memory retains observed neutral panel floor edges after they leave view. "
                    "Leave room for the whole rover body; an empty image center does not clear a side panel.")
        forward, lateral = self._local(pose[:2], self._origin)
        return (f" Camera obstacle detour: keep the {'right' if self._sign > 0 else 'left'} side route "
                f"near heading {self._travel_heading % 360:.1f} degrees. Measured original-route progress "
                f"is {forward:.2f} m forward and {abs(lateral):.2f} m sideways. Continue bounded clear "
                "travel until more than 0.30 m forward and 0.40 m sideways before steering back. "
                "If close contact prevents turning, retreat only along the previously observed clear approach.")
