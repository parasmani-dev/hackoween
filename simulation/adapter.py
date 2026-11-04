"""Synchronous engine client for the local Webots controller."""

import base64
import json
import socket


class WebotsAdapter:
    def __init__(self, port=8765, timeout=30):
        self.port = port
        self.timeout = timeout

    def _call(self, method, *args):
        with socket.create_connection(("127.0.0.1", self.port), self.timeout) as connection:
            connection.sendall(json.dumps({"method": method, "args": args}, allow_nan=False).encode() + b"\n")
            with connection.makefile("rb") as stream:
                response = json.loads(stream.readline(2_000_000))
        if not response["ok"]:
            raise RuntimeError("Webots action failed")
        return response["result"]

    def state(self):
        return self._call("state")

    def look(self):
        return base64.b64decode(self._call("look"), validate=True)

    def depth(self):
        return None

    def move(self, distance):
        return self._call("move", distance)

    def turn(self, degrees):
        return self._call("turn", degrees)

    def pickup(self):
        return self._call("pickup")

    def drop(self):
        return self._call("drop")

    def delivered_fixture_key(self) -> str | None:
        """Return private evaluator data for the latest qualifying drop."""
        return self._call("delivered_fixture_key")

    def reset(self):
        self._call("reset")

    def start_capture(self, command):
        return self._call("start_capture", command)

    def finish_capture(self):
        return self._call("finish_capture")

    def close(self):
        """Each request owns and closes its own connection."""
