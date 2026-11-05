"""Exercise the planner's first move from the two Rover Lab reset fixtures."""

import argparse
import json
import math
from io import BytesIO
from urllib.request import Request, urlopen

from PIL import Image

from integration.camera_clearance import observed_clearance
from integration.shortest_route import shortest_route

CELL_SIZE = 0.1
EMPTY_RADIUS = 0.35
CARRY_RADIUS = 0.72
GOAL = (12, 0)
CORRIDOR = {(step, 0) for step in range(GOAL[0] + 1)}


def reset_route(visible, *, surveyed_corridor, radius):
    """Authorize only the surveyed straight sweep to a visually clear endpoint."""
    if radius > EMPTY_RADIUS or GOAL not in visible:
        return None
    return shortest_route((0, 0), {GOAL}, CORRIDOR & surveyed_corridor)


def check(scenario, port):
    def call(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        with urlopen(Request(f"http://127.0.0.1:{port}{path}", data=body), timeout=15) as response:
            return response.read()

    call("/run/reset", {})
    call("/run/start", {"command": "Offline reset clearance probe"})
    try:
        pose = json.loads(call("/state"))
        if (math.hypot(pose["x"] + 2, pose["y"]) > 0.01
                or abs(math.remainder(pose["heading"], 360)) > 1 or pose["holding"]):
            raise RuntimeError("The rover is outside the empty reset fixture")
        with Image.open(BytesIO(call("/look"))) as frame:
            visible = observed_clearance(frame, CORRIDOR, cell_size=CELL_SIZE, radius=EMPTY_RADIUS)

        # Both original reset worlds (layout A for Scenario 2) have a
        # straight aisle along y=0 from x=-2.40 to x=-0.40. The entire
        # half-width of 0.40 m includes the grid cell and turning radius.
        # This survey is local fixture data, never an agent observation.
        for label, survey, radius in (
            ("unknown sweep", set(), EMPTY_RADIUS),
            ("payload outside survey", CORRIDOR, CARRY_RADIUS),
        ):
            if reset_route(visible, surveyed_corridor=survey, radius=radius) is not None:
                raise RuntimeError(f"Unexpected route for {label}")
            print(json.dumps({"scenario": scenario, "refused": label}), flush=True)

        route = reset_route(visible, surveyed_corridor=CORRIDOR, radius=EMPTY_RADIUS)
        if route is None:
            print(json.dumps({"scenario": scenario, "refused": "endpoint failed camera clearance",
                              "visible_cells": sorted(visible)}), flush=True)
            return False
        metres = (len(route) - 1) * CELL_SIZE
        result = json.loads(call("/move", {"distance": metres}))
        print(json.dumps({"scenario": scenario, "accepted_metres": metres, "result": result}),
              flush=True)
        if not result["success"] or result["collided"]:
            raise RuntimeError(f"Scenario {scenario}: reset move failed")
        return True
    finally:
        call("/run/reset", {})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-reset-fixtures", action="store_true", required=True,
                        help="Use only unchanged local worlds, with Scenario 2 set to layout A")
    parser.add_argument("--scenario-one-port", type=int, default=18836)
    parser.add_argument("--scenario-two-port", type=int, default=18846)
    args = parser.parse_args()
    first = check(1, args.scenario_one_port)
    second = check(2, args.scenario_two_port)
    raise SystemExit(0 if first and second else 1)
