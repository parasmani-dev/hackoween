"""Offline floor-clearance prototype for Rover Lab's calibrated front camera."""

from math import cos, radians, sin

from PIL import Image

from integration.shortest_route import Cell

_PITCH = radians(10)
_HEIGHT = 0.5
_OFFSET = 0.22


def _floor_pixel(forward, left):
    forward -= _OFFSET
    depth = forward * cos(_PITCH) + _HEIGHT * sin(_PITCH)
    if depth <= 0:
        return None
    down = _HEIGHT * cos(_PITCH) - forward * sin(_PITCH)
    return 319.5 - 320 * left / depth, 239.5 + 320 * down / depth


def observed_clearance(
    frame: Image.Image, cells: set[Cell], *, cell_size: float, radius: float
) -> set[Cell]:
    """Return locally visible clearance cells in rover-relative coordinates.

    Cells are (forward, left) indices. Radius must cover the turning sweep
    of the chassis and payload. Floor colors are specific to Rover Lab.
    Everything outside the image or failing the floor test stays unknown.
    A surface matching the floor color can be misclassified, so this
    heuristic needs scene validation before it can authorize motion.
    """
    if frame.size != (640, 480):
        raise ValueError("Expected a 640 × 480 front camera frame")
    if not 0 < cell_size <= 1 or not 0 <= radius <= 2:
        raise ValueError("Invalid cell size or clearance radius")

    pixels = frame.convert("RGB").load()
    # Summed-area table for rejecting any non-floor pixel in a projection.
    blocked = [[0] * 641]
    for y in range(480):
        row = [0]
        count = 0
        for x in range(640):
            red, green, blue = pixels[x, y]
            gray_floor = 145 <= min(red, green, blue) and max(red, green, blue) <= 175
            gray_floor = gray_floor and max(red, green, blue) - min(red, green, blue) <= 8
            count += not gray_floor
            row.append(count + blocked[-1][x + 1])
        blocked.append(row)

    clear = set()
    margin = radius + cell_size / 2
    for i, j in cells:
        corners = [
            _floor_pixel(i * cell_size + dx, j * cell_size + dy)
            for dx in (-margin, margin)
            for dy in (-margin, margin)
        ]
        if any(point is None for point in corners):
            continue
        xs, ys = zip(*corners)
        if min(xs) < 0 or max(xs) >= 640 or min(ys) < 0 or max(ys) >= 480:
            continue
        x0, x1 = int(min(xs)), int(max(xs)) + 1
        y0, y1 = int(min(ys)), int(max(ys)) + 1
        if blocked[y1][x1] - blocked[y0][x1] - blocked[y1][x0] + blocked[y0][x0] == 0:
            clear.add((i, j))
    return clear
