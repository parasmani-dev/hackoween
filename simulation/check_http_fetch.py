"""Exercise Vernit's HTTP server with a running Webots bridge."""

import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import threading
import time
from tempfile import mkdtemp
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from simulation.adapter import WebotsAdapter


def check(server, adapter, ui_root=None):
    root = f"http://127.0.0.1:{server.server_port}"

    def call(route, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        proxy = ui_root and route in ("/state", "/look", "/run/start", "/run/reset")
        url = (ui_root if proxy else root) + route
        headers = {"Origin": ui_root, "Content-Type": "application/json"} if proxy and data else {}
        with urlopen(Request(url, data=data, headers=headers), timeout=35) as response:
            if route == "/look":
                assert response.headers.get_content_type() == "image/jpeg"
                return response.read()
            return json.load(response)

    def state():
        result = call("/state")
        assert set(result) == {"state", "x", "y", "heading", "holding", "retry_count"}
        return result

    def face(x, y):
        pose = state()
        desired = -math.degrees(math.atan2(y - pose["y"], x - pose["x"]))
        degrees = math.remainder(desired - pose["heading"], 360)
        assert call("/turn", {"degrees": degrees}) == {"success": True}

    def drive(x, y):
        face(x, y)
        pose = state()
        distance = math.hypot(x - pose["x"], y - pose["y"])
        assert call("/move", {"distance": distance}) == {"success": True, "collided": False}

    command = {"command": "Fetch the red sphere and drop it in the green zone", "expected_fixture_key": "item_1"}
    assert call("/run/start", command) == {"state": "Locate"}
    initial = state()
    frame = call("/look")
    with Image.open(BytesIO(frame)) as image:
        assert image.format == "JPEG" and image.size == (640, 480)
        image.load()
    time.sleep(0.25)
    assert state() == initial and call("/look") == frame
    assert call("/pickup", {}) == {"success": False}
    assert call("/drop", {}) == {"success": False, "in_zone": False}

    assert adapter.delivered_fixture_key() is None
    try:
        call("/delivered_fixture_key")
    except HTTPError as error:
        with error:
            assert error.code == 404
    else:
        raise AssertionError("Private fixture key unexpectedly has an HTTP route")

    def fetch():
        drive(-1.01, 0.44)
        face(-0.55, 0.65)
        assert call("/pickup", {}) == {"success": True}, state()
        assert state()["holding"]
        drive(-0.65, state()["y"])
        drive(-0.65, -1.65)
        drive(1.28, -1.65)
        assert call("/drop", {}) == {"success": True, "in_zone": True}, state()
        assert state()["state"] == "Done" and not state()["holding"]
        assert adapter.delivered_fixture_key() == "item_1"

    capture_id = adapter.start_capture(command["command"])
    fetch()
    manifest_path = Path(adapter.finish_capture())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == capture_id and manifest["version"] == 1
    assert manifest["command"] == command["command"]
    frames = manifest["frames"]
    assert len(frames) > 20 and frames[0]["sim_time_ms"] == 0
    assert all(b["sim_time_ms"] > a["sim_time_ms"] for a, b in zip(frames, frames[1:]))
    for frame in (frames[0], frames[len(frames) // 2], frames[-1]):
        with Image.open(manifest_path.parent / frame["path"]) as image:
            assert image.size == (1280, 960) and image.format == "PNG"
            image.load()
    assert adapter.finish_capture() is None
    print(f"PASS: {len(frames)} native POV frames with a private manifest at {manifest_path}")
    assert call("/run/finish", {"reason": "abandoned"}) == {"state": "Done", "outcome": "pass"}
    print("PASS: full HTTP fetch and private pass verdict")
    assert call("/run/reset", {}) == {"state": "Idle"}
    assert adapter.delivered_fixture_key() is None

    assert call("/run/start", {**command, "expected_fixture_key": "item_2"}) == {"state": "Locate"}
    fetch()
    assert call("/run/finish", {"reason": "abandoned"}) == {"state": "Done", "outcome": "fail_wrong_object"}
    # Engine actions are gated after Done. Exercise a subsequent attempt directly.
    assert adapter.drop() == {"success": False, "in_zone": False}
    assert adapter.delivered_fixture_key() is None
    print("PASS: private key clears on reset and subsequent drop attempt")

    assert call("/run/start", command) == {"state": "Locate"}
    assert call("/move", {"distance": -2}) == {"success": False, "collided": True}
    blocked = state()
    assert blocked["state"] == "Blocked" and -2.73 < blocked["x"] < -2.5, blocked
    assert blocked["retry_count"] == 0
    assert call("/turn", {"degrees": 90}) == {"success": True}
    assert state()["state"] == "Blocked" and state()["retry_count"] == 0
    assert call("/move", {"distance": 0.5}) == {"success": True, "collided": False}
    assert state()["state"] == "Go to object" and state()["retry_count"] == 0
    print("PASS: post-contact turn and successful recovery through HTTP")

    assert call("/run/reset", {}) == {"state": "Idle"}
    assert call("/run/start", command) == {"state": "Locate"}
    assert call("/move", {"distance": -2}) == {"success": False, "collided": True}
    for retry in range(1, 4):
        assert call("/move", {"distance": -2}) == {"success": False, "collided": True}
        current = state()
        assert current["retry_count"] == retry
        assert current["state"] == ("Done" if retry == 3 else "Blocked")
    assert call("/run/finish", {"reason": "abandoned"}) == {"state": "Done", "outcome": "fail_collision"}
    print("PASS: wall contact, automatic backoff and preserved collision verdict")
    assert call("/run/reset", {}) == {"state": "Idle"}
    reset = state()
    assert abs(reset["x"] + 2) < 0.02 and not reset["holding"] and reset["retry_count"] == 0
    print("PASS: operator reset through HTTP")
    assert call("/run/start", command) == {"state": "Locate"}
    assert call("/run/finish", {"reason": "decision_limit"}) == {"state": "Done", "outcome": "fail_not_reached"}
    print("PASS: decision-budget finish through HTTP")


def run_live(server, adapter, args):
    root = f"http://127.0.0.1:{server.server_port}"
    output_root = Path("runs/live").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    evidence_dir = Path(mkdtemp(prefix="attempt-", dir=output_root))

    def operator_call(route, payload):
        request = Request(root + route, data=json.dumps(payload).encode())
        with urlopen(request, timeout=35) as response:
            return json.load(response)

    start = {"command": args.command}
    if args.expected_fixture_key:
        start["expected_fixture_key"] = args.expected_fixture_key
    if operator_call("/run/start", start)["state"] != "Locate":
        raise RuntimeError("Engine did not start the operator run")
    if args.record_pov:
        adapter.start_capture(args.command)

    status, exit_code = "abandoned", 130
    try:
        run = subprocess.run([
            sys.executable, "-m", "agent", args.command,
            "--provider", args.provider, "--engine-url", root,
            "--audit-requests", "--output-root", str(evidence_dir),
        ], cwd=args.agent_root, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        exit_code = run.returncode
        print(run.stdout, end="")
        try:
            status = json.loads(run.stdout).get("status", "unknown")
        except (ValueError, AttributeError):
            status = "unknown"
    except KeyboardInterrupt:
        print("Operator interrupted the run.")
    finally:
        reason = "decision_limit" if status == "decision_limit" else "abandoned"
        record = {"agent_status": status, "finish_reason": reason, "verdict": None}
        if args.expected_fixture_key:
            record["expected_fixture_key"] = args.expected_fixture_key
        try:
            record["verdict"] = operator_call("/run/finish", {"reason": reason})
            if args.record_pov:
                record["pov_manifest"] = adapter.finish_capture()
        except HTTPError as error:
            with error:
                record["finish_http_status"] = error.code
            print(f"Operator finish returned HTTP {error.code}. No rover action was replayed.")
            exit_code = exit_code or 1
        finally:
            evidence = evidence_dir / "operator-result.json"
            evidence.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            print(f"Operator result: {evidence}")
    (evidence_dir / "final-camera.jpg").write_bytes(adapter.look())
    if exit_code:
        raise SystemExit(exit_code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("engine_server", type=Path)
    parser.add_argument("--bridge-port", type=int, default=8765)
    parser.add_argument("--agent-root", type=Path)
    parser.add_argument("--provider", choices=("claude", "openai"), default="claude")
    parser.add_argument("--command")
    parser.add_argument("--expected-fixture-key", choices=[f"item_{i}" for i in range(1, 7)])
    parser.add_argument("--record-pov", action="store_true")
    parser.add_argument("--ui-server", type=Path)
    args = parser.parse_args()
    if args.command and args.ui_server:
        parser.error("--ui-server is for the offline rehearsal")
    if args.command:
        required = ("OPENAI_API_KEY", "OPENAI_MODEL") if args.provider == "openai" else (
            "ANTHROPIC_API_KEY", "CLAUDE_MODEL"
        )
        missing = [key for key in required if not os.getenv(key)]
        if missing:
            parser.error("Missing local configuration: " + ", ".join(missing))
        if not args.agent_root or not (args.agent_root / "agent" / "__main__.py").is_file():
            parser.error("--agent-root must point to Archit's agent checkout")
    sys.path.insert(0, str(args.engine_server.resolve().parent))
    spec = importlib.util.spec_from_file_location("engine_server", args.engine_server)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    adapter = WebotsAdapter(port=args.bridge_port)
    for _ in range(50):
        try:
            adapter.reset()
            break
        except OSError:
            time.sleep(0.2)
    else:
        raise RuntimeError("Webots bridge did not start")

    with engine.create_server(adapter, port=8080 if args.ui_server else 0) as server:
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        ui_process = None
        try:
            if args.command:
                run_live(server, adapter, args)
                return
            if args.ui_server:
                ui_root = "http://127.0.0.1:18090"
                ui_process = subprocess.Popen(
                    ["node", str(args.ui_server.resolve())],
                    env={**os.environ, "ROVER_UI_PORT": "18090"},
                )
                for _ in range(50):
                    if ui_process.poll() is not None:
                        raise RuntimeError("Operator UI stopped before becoming ready")
                    try:
                        with urlopen(ui_root, timeout=1) as response:
                            assert b"operator.js" in response.read()
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Operator UI did not become ready")
                check(server, adapter, ui_root)
                print("PASS: operator Start, camera, status and Reset through the UI proxy")
            else:
                check(server, adapter)
        finally:
            if ui_process is not None:
                ui_process.terminate()
                ui_process.wait(timeout=5)
            server.shutdown()
            worker.join()


if __name__ == "__main__":
    main()
