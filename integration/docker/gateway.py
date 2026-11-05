"""Docker entry point for public HTTP and one Webots world at a time."""
import json
import os
import signal
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DASHBOARD = "http://127.0.0.1:8113"
SCENARIO_ONE = "http://127.0.0.1:8112"
PORT = int(os.environ.get("PORT", "10000"))
ROOT = Path(os.environ["ROVER_LAB_RUN_DIR"])


def allowed_origins(port):
    public_url = os.environ.get("ROVER_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if public_url:
        parsed = urlsplit(public_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Public URL must be an HTTP(S) origin without credentials or a path")
        return {f"{parsed.scheme}://{parsed.netloc}"}
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


ORIGINS = allowed_origins(PORT)


class Simulator:
    def __init__(self):
        self.current = None
        self.children = []
        self.lock = Lock()

    def stop(self):
        for child in self.children:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for child in self.children:
            child.wait()
        self.children.clear()
        self.current = None

    def start(self, scenario):
        self.stop()
        bridge = 18825 + 10 * scenario
        engine = bridge + 1
        folder = ROOT / f"scenario-{scenario}"
        folder.mkdir(parents=True, exist_ok=True)
        for filename in ("front.jpg", "front.jpg.json", "spectator.jpg"):
            (folder / filename).unlink(missing_ok=True)
        env = {
            **os.environ, "ROVER_WEBOTS_PORT": str(bridge), "ROVER_ENGINE_PORT": str(engine),
            "ROVER_PREVIEW_FILE": str(folder / "front.jpg"),
            "ROVER_SPECTATOR_FILE": str(folder / "spectator.jpg"),
            "ROVER_LAB_RUN_DIR": str(folder),
        }
        if scenario == 1:
            env.pop("ROVER_DEMO_LAYOUT")
        commands = [
            ["xvfb-run", "-a", "webots", "--batch", "--minimize", "--no-rendering",
             "--mode=fast", "--stdout", "--stderr", f"--port={1240 + scenario}",
             f"rover_lab/worlds/scenario-{scenario}.wbt"],
            [sys.executable, "-m", "integration.serve_preview",
             "--bridge-port", str(bridge), "--engine-port", str(engine)],
        ]
        if scenario == 1:
            env.update(ROVER_UI_PORT="8112", ROVER_OFFLINE_DEMO="1")
            commands.append(["node", "ui/server.mjs"])
        try:
            for command in commands:
                self.children.append(subprocess.Popen(command, env=env, start_new_session=True))
            ready = [f"http://127.0.0.1:{engine}/state"]
            if scenario == 1:
                ready.append(SCENARIO_ONE + "/offline-demo")
            deadline = monotonic() + 40
            while monotonic() < deadline:
                if not self.healthy():
                    break
                try:
                    for url in ready:
                        with urlopen(url, timeout=1):
                            pass
                    self.current = scenario
                    return
                except (URLError, TimeoutError):
                    sleep(0.25)
            raise RuntimeError("Simulator did not start")
        except Exception:
            self.stop()
            raise

    def healthy(self):
        return bool(self.children) and all(child.poll() is None for child in self.children)

    def busy(self):
        if not self.healthy():
            return False
        url = SCENARIO_ONE + "/offline-demo" if self.current == 1 else DASHBOARD + "/watch/status"
        with urlopen(url, timeout=3) as response:
            state = json.load(response)
        return state["status"] == "running" if self.current == 1 else state["running"]


simulator = Simulator()


class Gateway(BaseHTTPRequestHandler):
    def reply(self, status, body, headers):
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def error(self, status, message):
        self.reply(status, json.dumps({"error": message}).encode(),
                   {"Content-Type": "application/json", "Cache-Control": "no-store"})

    def forward(self, data=None):
        headers = {}
        if token := self.headers.get("X-Jury-Token"):
            headers["X-Jury-Token"] = token
        if data is not None:
            headers.update({"Origin": DASHBOARD, "Content-Type": "application/json"})
        try:
            try:
                response = urlopen(Request(DASHBOARD + self.path, data=data, headers=headers), timeout=35)
            except HTTPError as error:
                response = error
            with response:
                forwarded = {key: response.headers[key] for key in
                             ("Content-Type", "Cache-Control", "Referrer-Policy", "X-Frame-State")
                             if key in response.headers}
                self.reply(response.code, response.read(), forwarded)
        except (URLError, TimeoutError):
            self.error(503, "Dashboard unavailable")

    def do_GET(self):
        path = urlsplit(self.path).path
        scenario = {"/scenario-1": 1, "/jury": 2}.get(path)
        if scenario:
            with simulator.lock:
                if scenario != simulator.current or not simulator.healthy():
                    try:
                        if simulator.busy():
                            return self.reply(409, b"Run in progress. Wait for it to finish, then reload.",
                                              {"Content-Type": "text/plain; charset=utf-8"})
                        simulator.start(scenario)
                    except (OSError, RuntimeError, URLError, TimeoutError):
                        return self.error(503, "Simulator is unavailable. Try again.")
        self.forward()

    def do_POST(self):
        if self.headers.get("Origin") not in ORIGINS:
            return self.error(403, "Origin rejected")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 1024:
                raise ValueError
        except ValueError:
            return self.error(400, "Invalid request size")
        self.connection.settimeout(5)
        try:
            data = self.rfile.read(size)
        except TimeoutError:
            return self.error(408, "Request timed out")
        path = urlsplit(self.path).path
        scenario = 1 if path.startswith("/scenario-1/api/") else 2
        with simulator.lock:
            if simulator.current != scenario or not simulator.healthy():
                return self.error(409, "Open this scenario again before starting a run.")
            self.forward(data)

    def log_message(self, format, *args):
        if self.command == "POST":
            super().log_message(format, *args)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Gateway).serve_forever()
