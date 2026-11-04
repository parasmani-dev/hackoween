import json
import logging
import math
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image, ImageDraw


STATE_FIELDS = ("state", "x", "y", "heading", "holding", "retry_count")
ACTION_ROUTES = {
    "/move": ("move", "distance", ("success", "collided")),
    "/turn": ("turn", "degrees", ("success",)),
    "/pickup": ("pickup", None, ("success",)),
    "/drop": ("drop", None, ("success", "in_zone")),
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


class EngineHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, adapter, **kwargs):
        self.adapter = adapter
        super().__init__(*args, **kwargs)

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == "/state":
                state = self.adapter.state()
                body = json.dumps(
                    {field: state[field] for field in STATE_FIELDS}, allow_nan=False
                ).encode("utf-8")
                content_type = "application/json"
            elif path == "/look":
                body = self.adapter.look()
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
            result = getattr(self.adapter, method)(*args)
            payload = {field: result[field] for field in response_fields}
            if any(type(value) is not bool for value in payload.values()):
                raise ValueError("Action results must contain boolean flags")
            body = json.dumps(payload).encode("utf-8")
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
        expected_fields = {parameter} if parameter is not None else set()
        if not isinstance(body, dict) or body.keys() != expected_fields:
            raise ValueError("Unexpected action fields")
        if parameter is None:
            return ()
        value = body[parameter]
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
    return HTTPServer(("127.0.0.1", port), partial(EngineHandler, adapter=adapter))


if __name__ == "__main__":
    with create_server(DiagnosticAdapter()) as server:
        server.serve_forever()
