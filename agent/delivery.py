"""Conservative green-floor arrival checks for the Rover Lab hybrid profile.

The map contains camera-projected, observed floor pixels and public rover poses.
The 0.60 m release offset is the Rover Lab attachment calibration, not an object
location. This helper is enabled only for an explicitly requested green zone.
"""

import math
import re

from PIL import Image


RELEASE_OFFSET = 0.60
DROP_MARGIN = 0.20


def _cross(origin, first, second):
    return ((first[0] - origin[0]) * (second[1] - origin[1])
            - (first[1] - origin[1]) * (second[0] - origin[0]))


def _hull(points):
    points = sorted(set(points))
    halves = []
    for sequence in (points, points[::-1]):
        half = []
        for point in sequence:
            while len(half) >= 2 and _cross(half[-2], half[-1], point) <= 0:
                half.pop()
            half.append(point)
        halves.append(half[:-1])
    return halves[0] + halves[1]


def _interior(polygon):
    area = sum(first[0] * second[1] - second[0] * first[1]
               for first, second in zip(polygon, polygon[1:] + polygon[:1]))
    if area <= 0:
        return None, 0.0
    center = tuple(sum((first[axis] + second[axis])
                       * (first[0] * second[1] - second[0] * first[1])
                       for first, second in zip(polygon, polygon[1:] + polygon[:1]))
                   / (3 * area) for axis in (0, 1))
    return center, area / 2


def _inside(polygon, point, margin):
    return bool(polygon) and all(
        _cross(first, second, point) >= margin * math.dist(first, second)
        for first, second in zip(polygon, polygon[1:] + polygon[:1])
    )


class GreenDropZone:
    """Remember one marked green floor patch through carried-object occlusion."""

    def __init__(self, command):
        normalized = command.lower().replace("-", " ") if isinstance(command, str) else ""
        self.enabled = bool(re.search(r"\bgreen\s+(?:(?:drop|delivery)\s+)?(?:zone|area)\b", normalized))
        self._polygon = []
        self._center = None
        self._area = 0.0
        self._search_point = None

    @property
    def known(self):
        return bool(self._polygon)

    @property
    def search_known(self):
        """A camera-derived search hint exists; this never authorizes release."""
        return self.known or self._search_point is not None

    @staticmethod
    def _bounds(pixels):
        return (min(index % 320 for index in pixels), min(index // 320 for index in pixels),
                max(index % 320 for index in pixels), max(index // 320 for index in pixels))

    @staticmethod
    def _markings(rgb, bounds):
        left, top, right, bottom = bounds
        markings = 0
        for y in range(max(100, top - 3), min(240, bottom + 4)):
            for x in range(max(0, left - 3), min(320, right + 4)):
                index = y * 320 + x
                r, g, b = rgb[index * 3:index * 3 + 3]
                if (g >= r * 1.03 and g >= b * 1.03 and min(r, g, b) > 120
                        and max(r, g, b) > 155 and max(r, g, b) - min(r, g, b) < 100):
                    markings += 1
        return markings

    def _remember_search(self, components, rgb, pose):
        if self.search_known:
            return False
        candidates = []
        for pixels in components:
            bounds = self._bounds(pixels)
            left, top, right, bottom = bounds
            width, height = right - left, bottom - top
            if (width < 8 or height < 2 or height / width >= 0.6
                    or bottom >= 238 or self._markings(rgb, bounds) < 2):
                continue
            points = [point for index in pixels
                      if (point := self._project(index % 320, index // 320, pose)) is not None]
            if len(points) < 15:
                continue
            candidates.append(points)
        if len(candidates) != 1:
            return False
        points = candidates[0]
        self._search_point = tuple(sum(point[axis] for point in points) / len(points)
                                   for axis in (0, 1))
        return True

    @staticmethod
    def _pose(state):
        try:
            values = tuple(state[key] for key in ("x", "y", "heading"))
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
                return None
            return values
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _project(u, v, pose):
        q = (v - 120.0) / 160.0
        tilt = math.radians(10)
        down = math.sin(tilt) + q * math.cos(tilt)
        if down <= 0.08:
            return None
        forward = 0.22 + 0.5 * (math.cos(tilt) - q * math.sin(tilt)) / down
        if not 0.45 <= forward <= 5.0:
            return None
        lateral = 0.5 * ((u - 160.0) / 160.0) / down
        angle = math.radians(pose[2])
        return (pose[0] + forward * math.cos(angle) - lateral * math.sin(angle),
                pose[1] - forward * math.sin(angle) - lateral * math.cos(angle))

    def observe(self, path, state):
        """Remember floor evidence before pickup and retain stronger views.

        Small/clipped fragments cannot replace a useful earlier polygon. The
        convex hull covers the observed patch, including its printed markings;
        no unseen rectangle dimensions or hidden corners are extrapolated.
        A small marked fragment supplies only a search direction, never a
        release polygon. True means new search or arrival evidence was acquired.
        """
        pose = self._pose(state)
        if not self.enabled or pose is None:
            return False
        try:
            with Image.open(path) as image:
                image = image.convert("RGB").resize((320, 240), Image.Resampling.NEAREST)
                rgb, hsv = image.tobytes(), image.convert("HSV").tobytes()
        except (OSError, ValueError, TypeError, Image.DecompressionBombError):
            return False
        mask = bytearray(320 * 240)
        for index in range(100 * 320, len(mask)):
            r, g, b = rgb[index * 3:index * 3 + 3]
            hue, saturation, value = hsv[index * 3:index * 3 + 3]
            mask[index] = (65 <= hue <= 116 and saturation >= 60 and value >= 40
                           and g > r * 1.12 and g > b * 1.12)
        components = []
        for seed in range(100 * 320, len(mask)):
            if mask[seed] != 1:
                continue
            queue = [seed]
            mask[seed] = 2
            offset = 0
            while offset < len(queue):
                index = queue[offset]
                offset += 1
                x, y = index % 320, index // 320
                for neighbor in ((index - 1 if x else -1),
                                 (index + 1 if x < 319 else -1),
                                 (index - 320 if y > 100 else -1),
                                 (index + 320 if y < 239 else -1)):
                    if neighbor >= 0 and mask[neighbor] == 1:
                        mask[neighbor] = 2
                        queue.append(neighbor)
            if len(queue) >= 20:
                components.append(queue)
        components.sort(key=len, reverse=True)
        search_acquired = self._remember_search(components, rgb, pose)
        components = [pixels for pixels in components if len(pixels) >= 80]
        if not components or (len(components) > 1 and len(components[1]) >= len(components[0]) * 0.65):
            return search_acquired
        pixels = components[0]
        left, top, right, bottom = self._bounds(pixels)
        if left <= 1 or right >= 318 or bottom >= 238 or right - left < 18 or bottom - top < 7:
            return search_acquired
        # Printed pale markings distinguish the floor zone from plain green
        # objects. Cream floor and teal wall fail the green-biased marking test.
        markings = self._markings(rgb, (left, top, right, bottom))
        if markings < 8:
            return search_acquired
        points = [point for index in pixels
                  if (point := self._project(index % 320, index // 320, pose)) is not None]
        polygon = _hull(points)
        center, area = _interior(polygon)
        if center is None or not 0.35 <= area <= 6.0 or not _inside(polygon, center, DROP_MARGIN):
            return search_acquired
        if any(max(point[axis] for point in polygon) - min(point[axis] for point in polygon) > 3.5
               for axis in (0, 1)):
            return search_acquired
        if self.known:
            # A partial or inconsistent near view must not shift the remembered
            # floor region under the carried object.
            if area < self._area * 1.2 or math.dist(center, self._center) > 0.35:
                return False
            if not _inside(polygon, self._center, DROP_MARGIN):
                return False
        self._polygon, self._center, self._area = polygon, center, area
        return True

    def ready(self, state):
        pose = self._pose(state)
        if (not self.enabled or not self.known or pose is None
                or state.get("holding") is not True or state.get("state") == "Blocked"):
            return False
        angle = math.radians(pose[2])
        release = (pose[0] + RELEASE_OFFSET * math.cos(angle),
                   pose[1] - RELEASE_OFFSET * math.sin(angle))
        return _inside(self._polygon, release, DROP_MARGIN)

    def _arrival(self, state):
        pose = self._pose(state)
        if not self.search_known or pose is None:
            return None
        target = self._center if self.known else self._search_point
        dx, dy = target[0] - pose[0], target[1] - pose[1]
        bearing = math.degrees(math.atan2(-dy, dx))
        return math.hypot(dx, dy), math.remainder(bearing - pose[2], 360)

    def guidance(self, state):
        if not self.enabled or state.get("holding") is not True:
            return ""
        if not self.known:
            if self.search_known and (arrival := self._arrival(state)) is not None:
                distance, angle = arrival
                return (
                    " Holding is confirmed. Keep carrying; do not pick up again. "
                    "An earlier camera view showed a small marked green floor patch, "
                    f"now about {distance:.2f} m away with bearing correction {angle:+.1f} degrees. "
                    "Use this only as a search direction, navigate around observed obstacles, "
                    "and reobserve the full marked floor region before dropping. "
                    "The remembered fragment does not prove a safe release position."
                )
            return " Holding is confirmed. Keep carrying; do not pick up again. Find a clearly marked green floor zone before dropping."
        arrival = self._arrival(state)
        if arrival is None:
            return " Holding is confirmed. Keep carrying; wait for a valid public rover pose before dropping."
        distance, angle = arrival
        return (" Holding is confirmed. Keep carrying; do not pick up again. "
                "The camera-grounded green floor region is remembered through occlusion. "
                f"Its safe interior is {distance:.2f} m from the rover, bearing correction {angle:+.1f} degrees. "
                f"The carried center releases {RELEASE_OFFSET:.2f} m ahead; "
                "drop only after that point is at least 0.20 m inside the observed floor region. "
                + ("The release point is safely inside now." if self.ready(state)
                   else "The release point is not safely inside yet; visible borders around the held object do not prove arrival."))

    def correction(self, state):
        """Suggest one short correction; the caller still applies motion guards."""
        if (state.get("holding") is not True or state.get("state") == "Blocked"
                or not self.enabled or not self.known or self.ready(state)):
            return None
        arrival = self._arrival(state)
        if arrival is None or arrival[0] > 2.25:
            return None
        distance, angle = arrival
        if abs(angle) > 10:
            return "turn", {"degrees": max(-25.0, min(25.0, angle))}
        remaining = distance - RELEASE_OFFSET
        if abs(remaining) < 0.005:
            return None
        return "move", {"distance": math.copysign(min(0.10, abs(remaining)), remaining)}
