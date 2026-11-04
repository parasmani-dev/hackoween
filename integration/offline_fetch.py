"""Run the fixed red-sphere rehearsal against an operator-started local engine."""

import json
import math
import sys
from urllib.request import Request, urlopen


def run(port):
    def call(path, data=None):
        body = None if data is None else json.dumps(data).encode()
        with urlopen(Request(f"http://127.0.0.1:{port}{path}", data=body), timeout=35) as response:
            return json.load(response)

    def face(x, y):
        pose = call("/state")
        heading = -math.degrees(math.atan2(y - pose["y"], x - pose["x"]))
        angle = math.remainder(heading - pose["heading"], 360)
        if not call("/turn", {"degrees": angle})["success"]:
            raise RuntimeError("Scripted turn was blocked")

    def drive(x, y):
        face(x, y)
        pose = call("/state")
        if not call("/move", {"distance": math.hypot(x - pose["x"], y - pose["y"])})["success"]:
            raise RuntimeError("Scripted move was blocked")

    try:
        drive(-0.96, 0.46)
        face(-0.55, 0.65)
        if not call("/pickup", {})["success"]:
            raise RuntimeError("Scripted pickup missed")
        drive(-0.65, call("/state")["y"])
        drive(-0.65, -1.65)
        drive(1.28, -1.65)
        if not call("/drop", {})["in_zone"]:
            raise RuntimeError("Scripted drop missed the zone")
    finally:
        verdict = call("/run/finish", {"reason": "abandoned"})
    if verdict["outcome"] != "pass":
        raise RuntimeError("Private evaluator did not confirm delivery")


if __name__ == "__main__":
    run(int(sys.argv[1]))
