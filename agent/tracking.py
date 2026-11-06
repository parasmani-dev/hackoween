"""Conservative image-only association of a clipped foreground component.

This tracker does not identify the requested object or select motion. A successful
update only means the acquired saturated component remained visually continuous.
"""

import math
from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class _Component:
    area: int
    box: tuple[int, int, int, int]
    center: tuple[float, float]
    hue: float


def _hue_distance(first, second):
    distance = abs(first - second)
    return min(distance, 256 - distance)


class ApproachTracker:
    """Acquire and associate one prominent lower-central clipped color region."""

    width = 160
    height = 120

    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self._previous = None
        self._anchor_hue = None

    def _components(self, path):
        try:
            with Image.open(path) as image:
                pixels = image.convert("RGB").resize(
                    (self.width, self.height), Image.Resampling.NEAREST
                ).convert("HSV").tobytes()
        except (OSError, ValueError, TypeError, AttributeError, Image.DecompressionBombError):
            return []
        hues, saturation, values = pixels[0::3], pixels[1::3], pixels[2::3]
        count = self.width * self.height
        foreground = bytearray(
            saturation[index] >= 115 and values[index] >= 46 for index in range(count)
        )
        visited = bytearray(count)
        components = []
        for seed in range(count):
            if visited[seed] or not foreground[seed]:
                continue
            visited[seed] = 1
            queue = [seed]
            offset = 0
            seed_hue = hues[seed]
            min_x = max_x = seed % self.width
            min_y = max_y = seed // self.width
            sum_x = sum_y = sum_sin = sum_cos = 0.0
            while offset < len(queue):
                index = queue[offset]
                offset += 1
                x, y = index % self.width, index // self.width
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)
                sum_x += x
                sum_y += y
                angle = hues[index] * math.tau / 256
                sum_sin += math.sin(angle)
                sum_cos += math.cos(angle)
                neighbors = []
                if x: neighbors.append(index - 1)
                if x + 1 < self.width: neighbors.append(index + 1)
                if y: neighbors.append(index - self.width)
                if y + 1 < self.height: neighbors.append(index + self.width)
                for neighbor in neighbors:
                    if (not visited[neighbor] and foreground[neighbor]
                            and _hue_distance(hues[neighbor], seed_hue) <= 16):
                        visited[neighbor] = 1
                        queue.append(neighbor)
            area = len(queue)
            components.append(_Component(
                area, (min_x, min_y, max_x + 1, max_y + 1),
                (sum_x / area, sum_y / area),
                math.atan2(sum_sin, sum_cos) * 256 / math.tau % 256,
            ))
        return components

    def _clipped_foreground(self, component):
        left, top, right, bottom = component.box
        return (component.area >= self.width * self.height * 0.025
                and right - left >= self.width * 0.12
                and bottom - top >= self.height * 0.15
                and bottom >= self.height - 2
                and component.center[1] >= self.height * 0.5)

    @staticmethod
    def _overlap(first, second):
        left = max(first[0], second[0])
        top = max(first[1], second[1])
        right = min(first[2], second[2])
        bottom = min(first[3], second[3])
        intersection = max(0, right - left) * max(0, bottom - top)
        union = ((first[2] - first[0]) * (first[3] - first[1])
                 + (second[2] - second[0]) * (second[3] - second[1]) - intersection)
        return intersection / union if union else 0.0

    def start(self, path):
        self.reset()
        candidates = [component for component in self._components(path)
                      if self._clipped_foreground(component)
                      and self.width * 0.25 <= component.center[0] <= self.width * 0.75]
        candidates.sort(key=lambda component: component.area, reverse=True)
        if not candidates or (len(candidates) > 1 and candidates[1].area >= candidates[0].area * 0.65):
            return False
        self._previous = candidates[0]
        self._anchor_hue = candidates[0].hue
        self.active = True
        return True

    def update(self, path):
        if not self.active:
            return False
        previous = self._previous
        candidates = []
        for component in self._components(path):
            if (not self._clipped_foreground(component)
                    or _hue_distance(component.hue, self._anchor_hue) > 12):
                continue
            jump = math.hypot((component.center[0] - previous.center[0]) / self.width,
                              (component.center[1] - previous.center[1]) / self.height)
            area_ratio = component.area / previous.area
            width_ratio = (component.box[2] - component.box[0]) / (previous.box[2] - previous.box[0])
            height_ratio = (component.box[3] - component.box[1]) / (previous.box[3] - previous.box[1])
            if (jump <= 0.12 and 0.8 <= area_ratio <= 1.55
                    and 0.8 <= width_ratio <= 1.35 and 0.8 <= height_ratio <= 1.5
                    and self._overlap(component.box, previous.box) >= 0.45):
                candidates.append(component)
        if len(candidates) != 1:
            self.reset()
            return False
        self._previous = candidates[0]
        return True
