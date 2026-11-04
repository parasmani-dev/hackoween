"""Private offline measurements, never imported by the model agent.

Fixed fixture waypoints are test inputs, not autonomous perception evidence.
Capture timestamps measure simulation cadence separately from wall time.
"""

from datetime import datetime
from io import BytesIO
import json
import math
from pathlib import Path
from tempfile import mkdtemp
import time
from urllib.request import Request, urlopen

from PIL import Image


def measure_timing(server, adapter):
    root = f"http://127.0.0.1:{server.server_port}"
    records = []

    def call(route, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        with urlopen(Request(root + route, data=data), timeout=35) as response:
            return response.read() if route == "/look" else json.load(response)

    def measure(label, action):
        before = call("/state")
        adapter.start_capture("Offline timing measurement")
        started = time.perf_counter()
        result = action()
        elapsed = time.perf_counter() - started
        manifest_path = Path(adapter.finish_capture())
        frames = json.loads(manifest_path.read_text())["frames"]
        after = call("/state")
        seconds = frames[-1]["sim_time_ms"] / 1000
        capture_seconds = (datetime.fromisoformat(frames[-1]["wall_time_utc"]) -
                           datetime.fromisoformat(frames[0]["wall_time_utc"])).total_seconds()
        record = dict(action=label, result=result, simulation_seconds=seconds,
                      action_wall_seconds=elapsed, frames=len(frames),
                      capture_wall_fps=(len(frames) - 1) / capture_seconds if capture_seconds > 0 else None,
                      displacement_metres=math.hypot(after["x"] - before["x"], after["y"] - before["y"]),
                      heading_change_degrees=math.remainder(after["heading"] - before["heading"], 360),
                      manifest=str(manifest_path))
        records.append(record)
        print("MEASURE: " + json.dumps({k: v for k, v in record.items() if k != "manifest"}))
        return record

    command = {"command": "Offline timing fixture", "expected_fixture_key": "item_1"}
    call("/run/start", command)

    def observe_without_motion():
        initial = call("/state")
        pixels = call("/look")
        with Image.open(BytesIO(pixels)) as image:
            assert image.format == "JPEG" and image.size == (640, 480)
        time.sleep(0.3)
        assert call("/state") == initial and call("/look") == pixels
        return {"unchanged": True}

    frozen = measure("wait_and_read", observe_without_motion)
    assert frozen["simulation_seconds"] == 0 and frozen["frames"] == 1
    movement = measure("move_0.5m", lambda: call("/move", {"distance": 0.5}))
    assert movement["result"] == {"success": True, "collided": False}
    assert abs(movement["simulation_seconds"] - 0.5) <= 0.15
    assert abs(movement["displacement_metres"] - 0.5) <= 0.015
    turning = measure("turn_90deg", lambda: call("/turn", {"degrees": 90}))
    assert turning["result"] == {"success": True}
    assert abs(turning["heading_change_degrees"] - 90) <= 3
    assert abs(turning["simulation_seconds"] - 1.0) <= 0.15

    call("/run/reset", {})
    call("/run/start", command)
    def face(x, y):
        pose = call("/state")
        degrees = math.remainder(-math.degrees(math.atan2(y - pose["y"], x - pose["x"])) - pose["heading"], 360)
        assert call("/turn", {"degrees": degrees}) == {"success": True}
    face(-0.96, 0.46)
    pose = call("/state")
    assert call("/move", {"distance": math.hypot(-0.96 - pose["x"], 0.46 - pose["y"])})["success"]
    face(-0.55, 0.65)
    pickup = measure("pickup", lambda: call("/pickup", {}))
    assert pickup["result"] == {"success": True}
    assert pickup["simulation_seconds"] == 0
    release = measure("out_of_zone_drop", lambda: call("/drop", {}))
    assert release["result"] == {"success": True, "in_zone": False}
    assert release["simulation_seconds"] == 0
    call("/run/finish", {"reason": "abandoned"})
    call("/run/reset", {})
    for distance in (-0.5, 0.1, 1.0):
        call("/run/start", command)
        measured = measure(f"move_{distance}m", lambda: call("/move", {"distance": distance}))
        assert measured["result"] == {"success": True, "collided": False}
        assert abs(measured["displacement_metres"] - abs(distance)) <= 0.015, measured
        assert abs(measured["simulation_seconds"] - abs(distance)) <= 0.15, measured
        call("/run/reset", {})
    for degrees in (-90, 45, 180):
        call("/run/start", command)
        measured = measure(f"turn_{degrees}deg", lambda: call("/turn", {"degrees": degrees}))
        assert measured["result"] == {"success": True}
        assert abs(math.remainder(measured["heading_change_degrees"] - degrees, 360)) <= 3, measured
        assert abs(measured["simulation_seconds"] - abs(degrees) / 90) <= 0.15, measured
        call("/run/reset", {})
    output = Path(mkdtemp(prefix="timing-", dir=Path("runs"))) / "measurements.json"
    output.write_text(json.dumps({"kind": "offline_scripted_measurements", "model_calls": 0,
                                  "records": records}, indent=2) + "\n")
    print(f"PASS: freeze/geometry checks; timing measurements (not a compliance waiver): {output.resolve()}")
