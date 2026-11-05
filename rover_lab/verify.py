"""Verify the local judge dashboard against a running real Webots demo."""
import json
import os
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

BASE = os.environ.get("ROVER_LAB_URL", "http://127.0.0.1:8113")
ROOT = Path(os.environ.get("ROVER_LAB_RUN_DIR", Path(__file__).resolve().parents[1] / "runs" / "rover-lab-8113"))


@lru_cache
def jury_token(page="/jury"):
    with urlopen(BASE + page, timeout=3) as response:
        html = response.read().decode()
    return re.search(r'<meta name="jury-token" content="([^"]+)"', html)[1]


def call(path, data=None, *, page="/jury"):
    request = Request(BASE + path, data=None if data is None else json.dumps(data).encode(),
                      headers={"Origin": BASE, "Content-Type": "application/json", "X-Jury-Token": jury_token(page)})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def check_image(path, size, image_format, *, page="/jury"):
    request = Request(BASE + path, headers={"X-Jury-Token": jury_token(page)})
    with urlopen(request, timeout=5) as response:
        with Image.open(BytesIO(response.read())) as image:
            image.load()
            assert (image.format, image.size) == (image_format, size), (path, image.format, image.size)


def expect_select_error(layout, expected):
    try:
        call("/select", {"layout": layout})
    except HTTPError as error:
        with error:
            assert error.code == expected, (layout, error.code)
            assert isinstance(json.load(error), dict)
    else:
        raise AssertionError(f"Layout {layout!r} unexpectedly selected")


def check(layout, expected, probe=None):
    assert call("/select", {"layout": layout}) == {"ok": True}
    assert call("/status")["layout"] == layout
    call("/start", {"probe": probe})
    if layout == "A" and probe is None:
        expect_select_error("B", 409)
    limit = monotonic() + 85
    while monotonic() < limit:
        status = call("/status")
        if not status["running"]:
            break
        sleep(0.4)
    else:
        raise RuntimeError("Rehearsal timed out")
    result = status["result"]
    print(json.dumps(result), flush=True)
    if result["verdict"] != expected:
        print(json.dumps(status["rows"], indent=2), flush=True)
        raise AssertionError(f"{layout} {probe}: {result}")
    replay = call("/replay/" + result["run_id"])
    assert replay == status["rows"]
    assert result["decisions"] == len(replay)
    assert any(row["tool"] == "pickup" and row["result"]["success"] for row in replay)
    turn = next(row for row in replay if row["tool"] == "turn" and row.get("after"))
    for field in ("frame_path", "after"):
        check_image("/replay/" + result["run_id"] + "/" + turn[field], (640, 480), "PNG")
    if probe == "collision":
        assert result["collisions"] >= 1
        events = [row["event"] for row in replay if "event" in row]
        assert events.index("Collision") < events.index("Recovered")
    return result


def check_red_sphere():
    page = "/scenario-1"
    prefix = page + "/api"
    demo = call(prefix + "/offline-demo", page=page)
    assert demo["enabled"] and demo["command"] == "Fetch the red sphere and drop it in the green zone"
    if call(prefix + "/state", page=page)["state"] != "Idle":
        call(prefix + "/run/reset", {}, page=page)
    for path in ("/preview", "/spectator"):
        check_image(prefix + path, (1280, 960), "JPEG", page=page)
    check_image(prefix + "/look", (640, 480), "JPEG", page=page)
    call(prefix + "/run/start", {"command": demo["command"]}, page=page)
    limit = monotonic() + 85
    while monotonic() < limit:
        status = call(prefix + "/offline-demo", page=page)["status"]
        if status != "running":
            assert status == "pass", status
            print("Scenario 1: red sphere delivery passed", flush=True)
            return
        sleep(0.4)
    raise RuntimeError("Scenario 1 timed out")


if __name__ == "__main__":
    check_red_sphere()
    for path in ("/front", "/spectator"):
        check_image(path, (1280, 960), "JPEG")
    print("Both scenarios: 1280x960 display feeds verified", flush=True)
    for invalid_layout in ([], {}):
        expect_select_error(invalid_layout, 400)
    print("Scenario 2: array and object layouts returned HTTP 400", flush=True)
    matrix = [check(layout, "pass") for layout in "ABC"]
    wrong = check("A", "fail_wrong_object", "wrong")
    recovery = check("A", "pass", "collision")
    evidence = {"scripted_layouts": matrix, "wrong_object": wrong, "collision_recovery": recovery}
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "offline-report.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    call("/select", {"layout": "A"})
    assert call("/replay/" + recovery["run_id"])
