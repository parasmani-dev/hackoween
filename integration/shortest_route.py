"""Plan a minimum-distance route through observed, clearance-checked grid cells."""

from collections.abc import Collection
from heapq import heappop, heappush
from math import sqrt

Cell = tuple[int, int]
_DIAGONAL = sqrt(2)
_STEPS = (
    (1, 0, 1), (0, 1, 1), (-1, 0, 1), (0, -1, 1),
    (1, 1, _DIAGONAL), (-1, 1, _DIAGONAL),
    (-1, -1, _DIAGONAL), (1, -1, _DIAGONAL),
)


def shortest_route(
    start: Cell, goals: Collection[Cell], clear: Collection[Cell]
) -> list[Cell] | None:
    """Return start through goal, or None if no route is known.

    Each clear cell must be safe across its entire area for the rover's
    footprint, including rotation and the carried object when applicable.
    The caller supplies this clearance map and valid approach cells.
    Unobserved cells are excluded. Diagonals cannot cut blocked corners.
    """
    walkable = set(clear)
    destinations = walkable.intersection(goals)
    if start not in walkable or not destinations:
        return None

    def estimate(cell):
        x, y = cell
        distances = ((abs(x - gx), abs(y - gy)) for gx, gy in destinations)
        return min(max(dx, dy) + (_DIAGONAL - 1) * min(dx, dy) for dx, dy in distances)

    frontier = [(estimate(start), 0.0, start)]
    distance = {start: 0.0}
    previous = {}
    while frontier:
        _, travelled, cell = heappop(frontier)
        if travelled != distance[cell]:
            continue
        if cell in destinations:
            route = [cell]
            while cell != start:
                cell = previous[cell]
                route.append(cell)
            return route[::-1]

        x, y = cell
        for dx, dy, length in _STEPS:
            neighbor = (x + dx, y + dy)
            if neighbor not in walkable:
                continue
            if dx and dy and ((x + dx, y) not in walkable or (x, y + dy) not in walkable):
                continue
            candidate = travelled + length
            if candidate < distance.get(neighbor, float("inf")):
                distance[neighbor] = candidate
                previous[neighbor] = cell
                heappush(frontier, (candidate + estimate(neighbor), candidate, neighbor))
    return None
