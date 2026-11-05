"""Short approach distances from an observed floor contact and public odometry.

The model must explicitly report a visible object-to-floor contact before an
anchor can be acquired. A clipped or occluded bounding box is never projected
onto the floor. No object dimensions, scene positions, or evaluator data are used.
"""

import math

from .tracking import ApproachTracker, _hue_distance


class FloorApproach:
    """Preserve one camera-derived floor point through a straight approach.

    ``observe`` acquires only from a visible-base frame. ``distance`` associates
    the same component after its base becomes clipped and returns a local move
    of at most 10 cm. None means there is no trustworthy anchor; zero means a
    valid anchor leaves no safe positive advance. Call ``reset`` on a turn,
    contact, successful acquisition, or a deliberate target change.
    """

    def __init__(self):
        self._vision = ApproachTracker()
        self.reset()

    def reset(self):
        self._ground = None
        self._hue = None
        self._origin = None
        self._last_pose = None
        self._previous = None

    @staticmethod
    def _pose(state):
        try:
            pose = tuple(state[key] for key in ("x", "y", "heading"))
            if any(type(value) not in (int, float) or not math.isfinite(value)
                   for value in pose) or state.get("holding") is True:
                return None
            return tuple(float(value) for value in pose)
        except (KeyError, TypeError, AttributeError):
            return None

    @staticmethod
    def _local(point, pose):
        angle = math.radians(pose[2])
        dx, dy = point[0] - pose[0], point[1] - pose[1]
        return (dx * math.cos(angle) - dy * math.sin(angle),
                -dx * math.sin(angle) - dy * math.cos(angle))

    def _foreground(self, component, *, clipped):
        left, top, right, bottom = component.box
        width, height = self._vision.width, self._vision.height
        return (component.area >= width * height * 0.02
                and width * 0.10 <= right - left <= width * 0.85
                and bottom - top >= height * 0.15
                and left > 1 and right < width - 1
                and width * 0.35 <= component.center[0] <= width * 0.65
                and component.center[1] >= height * 0.48
                and (bottom >= height - 2 if clipped
                     else height * 0.60 <= bottom <= height - 4))

    def observe(self, path, state, base_status):
        """Acquire a unique centered component with an explicitly visible base.

        A non-visible report does not replace an existing anchor. Movement or
        tracking invalidation is checked independently by ``distance``.
        """
        if base_status != "visible":
            return False
        self.reset()
        pose = self._pose(state)
        if pose is None:
            return False
        candidates = [component for component in self._vision._components(path)
                      if self._foreground(component, clipped=False)]
        if len(candidates) != 1:
            return False
        component = candidates[0]
        # The downsampled camera has f=80, cx=80, cy=60. Use the lower
        # pixel boundary, which conservatively shortens the projected range.
        left, _, right, bottom = component.box
        q = (bottom - 60.0) / 80.0
        tilt = math.radians(10.0)
        down = math.sin(tilt) + q * math.cos(tilt)
        forward = 0.22 + 0.5 * (math.cos(tilt) - q * math.sin(tilt)) / down
        lateral = 0.5 * (((left + right) / 2 - 80.0) / 80.0) / down
        if not 0.60 <= forward <= 2.50:
            return False
        angle = math.radians(pose[2])
        self._ground = (pose[0] + forward * math.cos(angle) - lateral * math.sin(angle),
                        pose[1] - forward * math.sin(angle) - lateral * math.cos(angle))
        self._hue = component.hue
        self._origin = self._last_pose = pose
        self._previous = component
        return True

    def distance(self, path, state):
        """Return a bounded move, None for lost evidence, or zero at clearance.

        Keep 2 cm for pixel/pose uncertainty, 26 cm for the front body extent,
        and at least 4 cm of clearance. Away from that bound, aim for a 10 cm
        margin before reverting to 2 cm pickup probes. Every move still needs a
        fresh camera frame and a pickup feedback check in the caller.
        """
        if self._ground is None:
            return None
        pose = self._pose(state)
        if pose is None:
            self.reset()
            return None
        moved = math.hypot(pose[0] - self._origin[0], pose[1] - self._origin[1])
        increment = math.hypot(pose[0] - self._last_pose[0], pose[1] - self._last_pose[1])
        along, sideways = self._local((pose[0], pose[1]), self._origin)
        if (abs(math.remainder(pose[2] - self._origin[2], 360)) > 3.0
                or moved > 1.25 or increment > 0.60
                or along < -0.03 or abs(sideways) > 0.06):
            self.reset()
            return None
        foreground = [component for component in self._vision._components(path)
                      if self._foreground(component, clipped=True)]
        # An additional central foreground object makes acquisition ambiguous,
        # even if it has a different hue. Never choose the largest by default.
        if len(foreground) != 1 or _hue_distance(foreground[0].hue, self._hue) > 12:
            self.reset()
            return None
        component = foreground[0]
        previous = self._previous
        old_forward, _ = self._local(self._ground, self._last_pose)
        forward, lateral = self._local(self._ground, pose)
        scale = max(1.0, (old_forward - 0.22) / max(0.05, forward - 0.22))
        width_ratio = ((component.box[2] - component.box[0])
                       / (previous.box[2] - previous.box[0]))
        area_ratio = component.area / previous.area
        horizontal_jump = abs(component.center[0] - previous.center[0]) / self._vision.width
        if (horizontal_jump > 0.12 or abs(lateral) > 0.16
                or not 0.70 <= width_ratio <= min(4.0, scale * 1.6)
                or not 0.70 <= area_ratio <= min(12.0, scale * scale * 1.8)):
            self.reset()
            return None
        self._previous, self._last_pose = component, pose
        conservative_forward = forward - 0.02
        hard_bound = conservative_forward - 0.26 - 0.04
        if hard_bound <= 0.005:
            return 0.0
        distance = min(0.10, max(0.02, conservative_forward - 0.26 - 0.10), hard_bound)
        if distance > 0.02:
            # Retain overlap for the caller's conservative image tracker:
            # the next frame should grow by less than its 1.35 width limit.
            distance = min(distance, max(0.02, 0.22 * (forward - 0.22)))
        # Round downward so serialization cannot exceed a geometric bound.
        return math.floor(distance * 1_000_000) / 1_000_000
