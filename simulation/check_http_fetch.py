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
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

from simulation.adapter import WebotsAdapter


def check(server, server_adapter):
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

    initial = state()
    frame = call("/look")
    with Image.open(BytesIO(frame)) as image:
        assert image.format == "JPEG" and image.size == (640, 480)
        image.load()
    time.sleep(0.25)
    assert state() == initial and call("/look") == frame
    assert call("/pickup", {}) == {"success": False}
    assert call("/drop", {}) == {"success": False, "in_zone": False}

    drive(-1.01, 0.44)
    face(-0.55, 0.65)
    assert call("/pickup", {}) == {"success": True}, state()
    assert state()["holding"]
    drive(-0.65, state()["y"])
    drive(-0.65, -1.65)
    drive(1.28, -1.65)
    assert call("/drop", {}) == {"success": True, "in_zone": True}, state()
    assert state()["state"] == "Done" and not state()["holding"]
    print("PASS: full motor-driven fetch through HTTP")

    server_adapter.reset()
    assert call("/move", {"distance": -2}) == {"success": False, "collided": True}
    blocked = state()
    assert blocked["state"] == "Blocked" and -2.73 < blocked["x"] < -2.5, blocked
    print("PASS: wall contact and automatic backoff through HTTP")


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
                run = subprocess.run([
                    sys.executable, "-m", "agent", args.command,
                    "--provider", args.provider, "--engine-url", root,
                    "--audit-requests", "--output-root", str(Path("runs/live").resolve()),
                ], cwd=args.agent_root)
                if run.returncode:
                    raise SystemExit(run.returncode)
                evidence = Path("runs/live/final-camera.jpg")
                evidence.write_bytes(adapter.look())
                print(f"Final rover camera: {evidence.resolve()}")
                print("Record the private delivery verdict separately from the agent stop reason.")
                return
            check(server, adapter)
            adapter.reset()
            state = adapter.state()
            assert state["state"] == "Idle" and abs(state["x"] + 2) < 0.02 and not state["holding"]
            print("PASS: private simulator reset")
        finally:
            server.shutdown()
            worker.join()


if __name__ == "__main__":
    main()
