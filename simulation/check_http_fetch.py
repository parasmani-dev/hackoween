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


def check(server, adapter):
    root = f"http://127.0.0.1:{server.server_port}"

    def call(route, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        with urlopen(Request(root + route, data=data), timeout=35) as response:
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

    command = {"command": "Fetch the red sphere and drop it in the green zone"}
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

    fetch()
    print("PASS: full motor-driven fetch through HTTP and private delivery key")
    assert call("/run/reset", {}) == {"state": "Idle"}
    assert adapter.delivered_fixture_key() is None

    assert call("/run/start", command) == {"state": "Locate"}
    fetch()
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
    print("PASS: wall contact, automatic backoff and three HTTP retries")
    assert call("/run/reset", {}) == {"state": "Idle"}
    reset = state()
    assert abs(reset["x"] + 2) < 0.02 and not reset["holding"] and reset["retry_count"] == 0
    print("PASS: operator reset through HTTP")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("engine_server", type=Path)
    parser.add_argument("--bridge-port", type=int, default=8765)
    parser.add_argument("--agent-root", type=Path)
    parser.add_argument("--provider", choices=("claude", "openai"), default="claude")
    parser.add_argument("--command")
    args = parser.parse_args()
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

    with engine.create_server(adapter, port=0) as server:
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            if args.command:
                root = f"http://127.0.0.1:{server.server_port}"
                request = Request(root + "/run/start", data=json.dumps({"command": args.command}).encode())
                with urlopen(request, timeout=35) as response:
                    assert json.load(response)["state"] == "Locate"
                output_root = Path("runs/live").resolve()
                output_root.mkdir(parents=True, exist_ok=True)
                evidence_dir = Path(mkdtemp(prefix="attempt-", dir=output_root))
                run = subprocess.run([
                    sys.executable, "-m", "agent", args.command,
                    "--provider", args.provider, "--engine-url", root,
                    "--audit-requests", "--output-root", str(evidence_dir),
                ], cwd=args.agent_root)
                evidence = evidence_dir / "final-camera.jpg"
                evidence.write_bytes(adapter.look())
                print(f"Final rover camera: {evidence.resolve()}")
                print("Record the private delivery verdict separately from the agent stop reason.")
                if run.returncode:
                    raise SystemExit(run.returncode)
                return
            check(server, adapter)
        finally:
            server.shutdown()
            worker.join()


if __name__ == "__main__":
    main()
