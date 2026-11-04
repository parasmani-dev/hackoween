import json
import logging
import math
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image, ImageDraw

if __package__:
    from .run_control import FIXTURE_KEYS, RunConflict, RunControl
else:
    from run_control import FIXTURE_KEYS, RunConflict, RunControl


STATE_FIELDS = ("state", "x", "y", "heading", "holding", "retry_count")
ACTION_ROUTES = {
    "/move": ("move", "distance", ("success", "collided")),
    "/turn": ("turn", "degrees", ("success",)),
    "/pickup": ("pickup", None, ("success",)),
    "/drop": ("drop", None, ("success", "in_zone")),
    "/run/start": ("start", "command", ()),
    "/run/reset": ("reset", None, ()),
    "/run/finish": ("finish", "reason", ()),
}
MAX_ACTION_BODY = 1024


class DiagnosticAdapter:
    """Provide a stationary diagnostic view before Webots is connected."""

    def __init__(self):
        image = Image.new("RGB", (640, 480), "#263448")
        ImageDraw.Draw(image).text((20, 20), "Diagnostic camera", fill="white")
        with BytesIO() as output:
            image.save(output, format="JPEG")
            self.frame = output.getvalue()

    def state(self):
        return dict(state="Idle", x=0.0, y=0.0, heading=0.0, holding=False, retry_count=0)

    def look(self):
        return self.frame

    def move(self, distance):
        return {"success": True, "collided": False}

    def turn(self, degrees):
        return {"success": True}

    def pickup(self):
        return {"success": False}

    def drop(self):
        return {"success": False, "in_zone": False}

    def reset(self):
        pass


class EngineHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, control, **kwargs):
        self.control = control
        super().__init__(*args, **kwargs)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == "/state":
                state = self.control.state()
                body = json.dumps(
                    {field: state[field] for field in STATE_FIELDS}, allow_nan=False
                ).encode("utf-8")
                content_type = "application/json"
            elif path == "/look":
                body = self.control.look()
                content_type = "image/jpeg"
            else:
                self.write_response(404, "application/json", b'{"error":"not_found"}')
                return
        except Exception:
            logging.exception("Engine read failed")
            self.write_response(500, "application/json", b'{"error":"engine_read_failed"}')
            return

        self.write_response(200, content_type, body)

    def do_POST(self):
        route = ACTION_ROUTES.get(urlsplit(self.path).path)
        if route is None:
            self.write_response(404, "application/json", b'{"error":"not_found"}')
            return

        method, parameter, response_fields = route
        try:
            args = self.read_action_args(parameter)
        except (ValueError, OverflowError):
            self.write_response(400, "application/json", b'{"error":"invalid_action_body"}')
            return

        try:
            payload = self.control.execute(method, args, response_fields)
            body = json.dumps(payload).encode("utf-8")
        except RunConflict as conflict:
            body = json.dumps({"error": str(conflict)}).encode("utf-8")
            self.write_response(409, "application/json", body)
            return
        except Exception:
            logging.exception("Engine action failed")
            self.write_response(500, "application/json", b'{"error":"engine_action_failed"}')
            return

        self.write_response(200, "application/json", body)

    def read_action_args(self, parameter):
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
            raise ValueError("Expected one Content-Length")
        size = int(lengths[0])
        if not 0 < size <= MAX_ACTION_BODY:
            raise ValueError("Invalid action body length")
        raw_body = self.rfile.read(size)
        if len(raw_body) != size:
            raise ValueError("Incomplete action body")
        body = json.loads(raw_body.decode("utf-8"))
        if parameter == "command":
            if not isinstance(body, dict) or body.keys() - {"command", "expected_fixture_key"}:
                raise ValueError("Unexpected start fields")
            command = body.get("command")
            if not isinstance(command, str) or not command.strip():
                raise ValueError("Expected a non-empty command")
            key = body.get("expected_fixture_key")
            if key is not None and (not isinstance(key, str) or key not in FIXTURE_KEYS):
                raise ValueError("Invalid expected fixture key")
            return (key,)
        expected_fields = {parameter} if parameter is not None else set()
        if not isinstance(body, dict) or body.keys() != expected_fields:
            raise ValueError("Unexpected action fields")
        if parameter is None:
            return ()
        value = body[parameter]
        if parameter == "reason":
            if value not in ("decision_limit", "abandoned"):
                raise ValueError("Invalid finish reason")
            return ()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Expected a finite number")
        return (value,)

    def write_response(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(adapter, port=8080):
    control = RunControl(adapter)
    return ThreadingHTTPServer(
        ("127.0.0.1", port), partial(EngineHandler, control=control)
    )


if __name__ == "__main__":
    with create_server(DiagnosticAdapter()) as server:
        server.serve_forever()
