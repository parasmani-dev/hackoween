"""Exercise HTTP motion validation before it can mutate an active run."""

import json
import math
from threading import Thread
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from engine.server import DiagnosticAdapter, create_server


class MotionValidationTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Mock(wraps=DiagnosticAdapter())
        self.server = create_server(self.adapter, port=0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.assertEqual(self.call("/run/start", {"command": "Fetch"})[0], 200)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def call(self, path, body=None):
        request = Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.code, json.load(response)

    def test_oversized_motion_is_rejected_without_poisoning_run(self):
        for name, field, limit in (("move", "distance", 10), ("turn", "degrees", 360)):
            action = getattr(self.adapter, name)
            for magnitude in (limit + 1, math.nextafter(limit, math.inf), 10 ** 400):
                for value in (-magnitude, magnitude):
                    with self.subTest(action=name, value=value):
                        before = self.call("/state")
                        action.reset_mock()
                        self.assertEqual(self.call("/" + name, {field: value}),
                                         (400, {"error": "invalid_action_body"}))
                        action.assert_not_called()
                        self.assertEqual(self.call("/state"), before)
                        self.assertEqual(self.call("/" + name, {field: 0})[0], 200)

    def test_inclusive_boundaries_are_dispatched(self):
        for name, field, limit in (("move", "distance", 10), ("turn", "degrees", 360)):
            action = getattr(self.adapter, name)
            for value in (-limit, 0, limit):
                with self.subTest(action=name, value=value):
                    action.reset_mock()
                    self.assertEqual(self.call("/" + name, {field: value})[0], 200)
                    action.assert_called_once_with(value)


if __name__ == "__main__":
    unittest.main()
