import json
import logging
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image, ImageDraw


STATE_FIELDS = ("state", "x", "y", "heading", "holding", "retry_count")


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
