"""Local, offline operator dashboard for the three-layout Webots rehearsal."""
import argparse
import json
import logging
import math
import os
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(REPO))
from agent.claude import Decision
from agent.engine import EngineClient
from agent.runner import run_command

COMMAND = "Fetch the blue cylinder beside the yellow cone and drop it in the green zone"
RED_CUBE_COMMAND = COMMAND.replace("yellow cone", "red cube")
SUPPORTED_COMMANDS = {command.casefold() for command in (COMMAND, RED_CUBE_COMMAND)}
LAYOUTS = json.loads((ROOT / "layouts.json").read_text(encoding="utf-8-sig"))
RUN_DIR = Path(os.environ.get("ROVER_LAB_RUN_DIR", REPO / "runs" / "rover-lab-8113"))
OUTPUT = RUN_DIR / "evidence"
SITE = ROOT / "site"
ASSETS = {
    "theme.css": "text/css; charset=utf-8", "feeds.js": "text/javascript; charset=utf-8",
    "watch.js": "text/javascript; charset=utf-8", "arena.jpg": "image/jpeg",
    "before.png": "image/png", "after.png": "image/png",
    "Geist-Variable.woff2": "font/woff2", "GeistMono-Variable.woff2": "font/woff2",
}
PAGES = {
    "/": SITE / "landing.html", "/scenario-1": SITE / "scenario-1.html",
    "/jury": ROOT / "dashboard.html", "/watch": SITE / "watch.html",
}
SCENARIO_ONE_API = "/scenario-1/api"
SCENARIO_ONE_GET = {"/state", "/offline-demo", "/look", "/preview", "/spectator"}
SCENARIO_ONE_POST = {"/run/start", "/run/reset"}
SCENARIO_ONE_ORIGIN = f'http://127.0.0.1:{int(os.environ.get("ROVER_LAB_SCENARIO_ONE_PORT", "8112"))}'


def navigation(path):
    links = "".join(
        f'<a href="{url}"' + (' aria-current="page"' if path == url else "") + f'>{label}</a>'
        for url, label in (("/jury", "Jury Demo"), ("/watch", "Watch Demo"))
    )
    return (
        '<header class="site-nav"><div class="nav-inner"><a class="brand" href="/">'
        '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">'
        '<path d="M5 20h22l-3-9H11l-6 9Zm11-9V6h5"/><circle cx="9" cy="24" r="3"/>'
        '<circle cx="23" cy="24" r="3"/><path d="M12 24h8"/></svg>Rover Lab</a>'
        f'<nav aria-label="Demo navigation">{links}</nav></div></header>'
    )


def is_viewer_replay(record):
    return (
        record.get("scripted") is True and record.get("verdict") == "pass"
        and record.get("command") == COMMAND and not record.get("custom") and not record.get("probe")
    )


def viewer_steps(folder):
    return [
        {key: row[key] for key in ("step", "tool", "event", "frame_path", "after") if key in row}
        for row in Demo.rows(folder) if row["tool"] in ("turn", "pickup") or row.get("event")
    ]


def watch_snapshot(demo):
    state = demo.status(viewer=True)
    rows = state["rows"]
    holding = False
    for row in rows:
        if row["tool"] in ("pickup", "drop") and row["result"].get("success") is True:
            holding = row["tool"] == "pickup"
    running = state["running"]
    latest = state["viewer_replay"]
    steps = viewer_steps(latest) if latest and not running else []
    replay = {"run_id": latest.name, "steps": steps} if steps else None
    return {
        "running": running,
        "label": ("Carrying the object" if holding else "Rover run in progress") if running
        else ("Latest rehearsal complete" if replay else "Waiting for a run"),
        "decisions": len(rows),
        "collisions": sum(row.get("event") == "Collision" for row in rows),
        "replay": replay,
    }

class ScriptedPolicy:
    """Known waypoints for exercising the real runner, without a model transport."""
    def __init__(self, layout, collision, wrong):
        route = []
        if collision:
            route += [("move", 0.74), ("turn", -90), ("move", 0.9), ("move", -0.28)]
        if wrong:
            points = [[-1.85, -0.58], [-1.31, -0.58]]
            target = [-0.85, -0.58]
            back = [[-1.45, -0.56], [-1.45, -1.87], [0.8, -1.87], [1.36, -1.65]]
        else:
            points, target, back = layout["approach"], layout["target"], layout["return"]
        for point in points:
            route += [("face", point), ("drive", point)]
        route += [("face", target), ("pickup", None)]
        for point in back:
            route += [("face", point), ("drive", point)]
        self.actions = iter(route + [("drop", None)])
        self.limit = len(route) + 1
        self.decisions = 0

    def decide(self, messages, *, system):
        pose = json.loads(messages[0]["content"][0]["text"])["state"]
        action, target = next(self.actions)
        self.decisions += 1
        if action in ("face", "drive"):
            dx, dy = target[0] - pose["x"], target[1] - pose["y"]
            if action == "drive":
                return Decision("move", {"distance": math.hypot(dx, dy)}, {})
            delta = -math.degrees(math.atan2(dy, dx)) - pose["heading"]
            return Decision("turn", {"degrees": math.remainder(delta, 360)}, {})
        field = {"turn": "degrees", "move": "distance"}.get(action)
        return Decision(action, {field: target} if field else {}, {})


class Demo:
    def __init__(self, engine_url):
        self.engine_url = engine_url
        self.layout_path = Path(os.environ["ROVER_DEMO_LAYOUT"])
        self.layout = self.layout_path.read_text(encoding="utf-8").strip()
        self.lock = threading.Lock()
        self.active = None
        self.running = False
        self.latest = None
        self.viewer_replay = None
        self.report = {}
        for summary in sorted(OUTPUT.glob("*/*/operator-summary.json"), key=lambda path: path.stat().st_mtime):
            try:
                record = json.loads(summary.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            self.latest = summary.parent
            if is_viewer_replay(record):
                self.viewer_replay = summary.parent
            if not record.get("probe") and record.get("command") == COMMAND and not record.get("custom"):
                self.report[record["layout"]] = record

    def engine(self, path, data):
        req = Request(self.engine_url + path, data=json.dumps(data).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=25) as response:
            return json.load(response)

    def select(self, layout):
        old = self.layout
        self.layout_path.write_text(layout, encoding="utf-8")
        try:
            self.engine("/run/reset", {})
        except Exception:
            self.layout_path.write_text(old, encoding="utf-8")
            raise
        self.layout = layout
        self.active = None

    @staticmethod
    def route(layout_key, command):
        if " ".join(command.casefold().split()).rstrip(".!?") != RED_CUBE_COMMAND.casefold():
            return LAYOUTS[layout_key]
        if layout_key == "B":
            return LAYOUTS["A"]
        if layout_key == "A":
            return LAYOUTS["B"]
        return {
            "expected": "item_3",
            "approach": [[-1.85, 0.58], [-1.31, 0.58]],
            "target": [-0.85, 0.58],
            "return": [[-1.60, 0.55], [-1.60, 0.05], [1.06, 0.05],
                       [1.06, -1.65], [1.35, -1.65]],
        }

    def run(self, layout_key, collision, wrong, command, custom):
        folder = None
        try:
            layout = self.route(layout_key, command)
            self.engine("/run/start", {"command": command, "expected_fixture_key": layout["expected"]})
            policy = ScriptedPolicy(layout, collision, wrong)
            client = EngineClient(self.engine_url, timeout=float(os.environ.get("ROVER_ACTION_TIMEOUT", "35")))
            result = run_command(command, engine=client, claude=policy,
                                 output_root=self.active, max_decisions=policy.limit)
            folder = result.run_dir
            verdict = self.engine("/run/finish", {"reason": "abandoned"})
            record = {"layout": layout_key, "command": command, "custom": custom, "verdict": verdict["outcome"],
                      "decisions": result.decisions, "stop": result.status, "scripted": True,
                      "probe": "collision" if collision else "wrong" if wrong else None}
        except Exception:
            logging.exception("Offline demo failed")
            try:
                self.engine("/run/finish", {"reason": "abandoned"})
            except Exception:
                logging.exception("Could not finalize the failed run")
            record = {"layout": layout_key, "verdict": "operator_error", "scripted": True}
        finally:
            with self.lock:
                if folder is None:
                    folder = self.run_folder(self.active)
                if folder:
                    record.update(run_id=folder.name)
                    record["collisions"] = sum(row["result"].get("collided") is True for row in self.rows(folder))
                    (folder / "operator-summary.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
                    self.latest = folder
                    if is_viewer_replay(record):
                        self.viewer_replay = folder
                if not custom and not collision and not wrong:
                    self.report[layout_key] = record
                self.running = False
                self.result = record

    @staticmethod
    def run_folder(attempt):
        return next(attempt.glob("run-*"), None) if attempt else None

    @staticmethod
    def rows(folder):
        journal = folder / "steps.jsonl" if folder else None
        if journal is None or not journal.is_file():
            return []
        lines = journal.read_text(encoding="utf-8").splitlines(keepends=True)
        rows = [json.loads(line) for line in lines if line.endswith("\n")]
        recovering = False
        for row in rows:
            outcome = row["result"]
            if outcome.get("collided") is True:
                row["event"] = "Collision"
                recovering = True
            elif row["tool"] == "move" and outcome.get("success") is True and recovering:
                row["event"] = "Recovered"
                recovering = False
            elif row["tool"] == "turn" and outcome.get("success") is False:
                row["event"] = "Turn failed"
            after = f'{row["step"]:04d}.png'
            if row["tool"] in ("move", "turn") and (folder / after).is_file():
                row["after"] = after
        return rows

    def status(self, *, viewer=False):
        with self.lock:
            folder = self.run_folder(self.active)
            rows = self.rows(folder)
            if viewer:
                return {"running": self.running, "rows": rows, "viewer_replay": self.viewer_replay}
            result = getattr(self, "result", None) if self.active and not self.running else None
            return {"layout": self.layout, "running": self.running, "result": result,
                    "run_id": folder.name if folder else None, "rows": rows,
                    "latest": self.latest.name if self.latest else None, "report": self.report.copy()}


def serve(port, engine_port):
    demo = Demo(f"http://127.0.0.1:{engine_port}")
    jury_token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, body, content="application/json", status=200, *, frame_state=None):
            if content == "application/json" and not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            if frame_state:
                self.send_header("X-Frame-State", frame_state)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionResetError, BrokenPipeError):
                pass

        def is_jury(self, query=""):
            token = self.headers.get("X-Jury-Token") or parse_qs(query).get("jury", [""])[0]
            return token.isascii() and secrets.compare_digest(token, jury_token)

        def camera(self, name):
            env = "ROVER_PREVIEW_FILE" if name == "front" else "ROVER_SPECTATOR_FILE"
            try:
                path = Path(os.environ[env])
                if name == "front":
                    heartbeat = json.loads(Path(str(path) + ".json").read_text())
                    if not heartbeat["ready"] or time.time() - heartbeat["heartbeat_ms"] / 1000 > 3:
                        return self.reply({}, status=503)
                age = time.time() - path.stat().st_mtime
                if demo.running and age > 3:
                    return self.reply({}, status=503)
                frame_state = "paused" if age > 3 else "live"
                return self.reply(path.read_bytes(), "image/jpeg", frame_state=frame_state)
            except (OSError, ValueError, KeyError):
                return self.reply({}, status=503)

        def scenario_one(self, path, data=None):
            headers = {}
            if data is not None:
                headers = {"Origin": SCENARIO_ONE_ORIGIN, "Content-Type": "application/json"}
            request = Request(SCENARIO_ONE_ORIGIN + path, data=data, headers=headers)
            try:
                with urlopen(request, timeout=12 if data is not None else 3) as response:
                    content = "image/jpeg" if path in {"/look", "/preview", "/spectator"} else "application/json"
                    return self.reply(response.read(), content, response.status)
            except HTTPError as error:
                error.close()
                return self.reply({"error": "Scenario 1 request failed"}, status=error.code)
            except (URLError, TimeoutError):
                return self.reply({"error": "Scenario 1 is unavailable"}, status=503)

        def do_GET(self):
            url = urlsplit(self.path)
            path = url.path
            if path in PAGES:
                html = PAGES[path].read_text(encoding="utf-8-sig")
                html = html.replace("<!-- SITE NAV -->", navigation(path))
                if path in {"/jury", "/scenario-1"}:
                    html = html.replace("JURY_TOKEN", jury_token)
                return self.reply(html.encode(), "text/html; charset=utf-8")
            if path == "/scenario-1/operator.js":
                script = REPO / "ui" / "operator.js"
                return self.reply(script.read_bytes(), "text/javascript; charset=utf-8")
            if path.startswith(SCENARIO_ONE_API + "/"):
                if not self.is_jury(url.query):
                    return self.reply({}, status=403)
                upstream = path.removeprefix(SCENARIO_ONE_API)
                if upstream in SCENARIO_ONE_GET:
                    return self.scenario_one(upstream)
                return self.reply({}, status=404)
            if path.startswith("/assets/") and (asset := path.removeprefix("/assets/")) in ASSETS:
                return self.reply((SITE / asset).read_bytes(), ASSETS[asset])
            if path == "/watch/status":
                return self.reply(watch_snapshot(demo))
            if path in ("/watch/front", "/watch/spectator"):
                if not demo.running:
                    return self.reply({}, status=503)
                return self.camera(path.rsplit("/", 1)[1])
            watch_frame = re.fullmatch(r"/watch/replay/(run-[a-z0-9_]+)/([0-9]{4}\.png)", path)
            if watch_frame:
                folder = next(OUTPUT.glob(f"*/{watch_frame[1]}"), None)
                if folder:
                    try:
                        record = json.loads((folder / "operator-summary.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        return self.reply({}, status=404)
                    if is_viewer_replay(record):
                        approved = {row.get(key) for row in viewer_steps(folder) for key in ("frame_path", "after")}
                        frame = folder / watch_frame[2]
                        if watch_frame[2] in approved and frame.is_file():
                            return self.reply(frame.read_bytes(), "image/png")
                return self.reply({}, status=404)
            if not self.is_jury(url.query):
                return self.reply({}, status=403)
            if path == "/status":
                return self.reply(demo.status())
            if path in ("/front", "/spectator"):
                return self.camera(path[1:])
            run = re.fullmatch(r"/replay/(run-[a-z0-9_]+)(?:/([0-9]{4}\.png))?", path)
            if run:
                folder = next(OUTPUT.glob(f"*/{run[1]}"), None)
                if folder:
                    if not run[2]:
                        return self.reply(demo.rows(folder))
                    frame = folder / run[2]
                    if frame.is_file():
                        return self.reply(frame.read_bytes(), "image/png")
            self.reply({}, status=404)

        def do_POST(self):
            if self.headers.get("Origin") != f"http://127.0.0.1:{port}" or not self.is_jury():
                return self.reply({}, status=403)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 1024:
                    raise ValueError
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, TypeError):
                return self.reply({}, status=400)
            if self.path.startswith(SCENARIO_ONE_API + "/"):
                upstream = self.path.removeprefix(SCENARIO_ONE_API)
                if upstream in SCENARIO_ONE_POST:
                    return self.scenario_one(upstream, json.dumps(data).encode())
                return self.reply({}, status=404)
            with demo.lock:
                if demo.running:
                    return self.reply({"error": "Run already active"}, status=409)
                if self.path == "/select":
                    layout = data.get("layout")
                    if not isinstance(layout, str) or layout not in LAYOUTS:
                        return self.reply({}, status=400)
                    try:
                        demo.select(layout)
                    except Exception:
                        logging.exception("Layout reset failed")
                        return self.reply({"error": "Layout reset failed"}, status=502)
                elif self.path == "/start":
                    collision = data.get("probe") == "collision"
                    wrong = data.get("probe") == "wrong"
                    if data.get("probe") not in (None, "collision", "wrong") or (
                        (collision or wrong) and demo.layout != "A"
                    ):
                        return self.reply({"error": "Probes require layout A"}, status=400)
                    custom = "command" in data
                    command = data.get("command", COMMAND)
                    if custom and (
                        collision or wrong or not isinstance(command, str)
                        or " ".join(command.casefold().split()).rstrip(".!?") not in SUPPORTED_COMMANDS
                    ):
                        return self.reply({
                            "error": "This command needs the live agent. Choose an offline example below."
                        }, status=400)
                    command = command.strip()
                    from tempfile import mkdtemp
                    OUTPUT.mkdir(parents=True, exist_ok=True)
                    demo.active = Path(mkdtemp(prefix="attempt-", dir=OUTPUT))
                    demo.running = True
                    threading.Thread(target=demo.run, args=(demo.layout, collision, wrong, command, custom), daemon=True).start()
                else:
                    return self.reply({}, status=404)
            self.reply({"ok": True})

        def log_message(self, format, *args):
            if self.command == "POST":
                super().log_message(format, *args)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Judge dashboard at http://127.0.0.1:{port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8113)
    parser.add_argument("--engine-port", type=int, default=18846)
    args = parser.parse_args()
    serve(args.port, args.engine_port)
