"""Keep the slow-rendering Docker timeout budget consistent across clients."""
import io
import json
import os
import runpy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from integration import offline_fetch
from rover_lab import dashboard


class RuntimeTimeoutTests(unittest.TestCase):
    def test_engine_adapter_uses_configured_bridge_timeout(self):
        for environment, expected in (({}, 30), ({"ROVER_WEBOTS_TIMEOUT": "60"}, 60)):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                 patch("sys.argv", ["serve_preview", "--bridge-port", "8765", "--engine-port", "8080"]), \
                 patch("simulation.adapter.WebotsAdapter") as adapter, \
                 patch("engine.server.create_server", return_value=MagicMock()):
                runpy.run_module("integration.serve_preview", run_name="__main__")
                adapter.assert_called_once_with(port=8765, timeout=expected)

    def test_red_script_uses_configured_action_timeout(self):
        payload = json.dumps({"x": 0, "y": 0, "heading": 0, "success": True,
                              "in_zone": True, "outcome": "pass"}).encode()
        for environment, expected in (({}, 35), ({"ROVER_ACTION_TIMEOUT": "65"}, 65)):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                 patch.object(offline_fetch, "urlopen", side_effect=lambda *a, **k: io.BytesIO(payload)) as transport:
                offline_fetch.run(8080)
                self.assertGreater(transport.call_count, 1)
                self.assertTrue(all(call.kwargs["timeout"] == expected for call in transport.call_args_list))

    def test_jury_script_uses_configured_action_timeout(self):
        for environment, expected in (({}, 35), ({"ROVER_ACTION_TIMEOUT": "65"}, 65)):
            with self.subTest(environment=environment), TemporaryDirectory() as directory, \
                 patch.dict(os.environ, environment, clear=True), \
                 patch.object(dashboard, "EngineClient") as client, \
                 patch.object(dashboard, "run_command", return_value=SimpleNamespace(
                     run_dir=Path(directory), decisions=0, status="engine_done")):
                demo = object.__new__(dashboard.Demo)
                demo.engine_url = "http://127.0.0.1:8080"
                demo.engine = Mock(return_value={"outcome": "pass"})
                demo.active = Path(directory)
                demo.lock = Lock()
                demo.report = {}
                demo.run("A", False, False, dashboard.COMMAND, False)
                client.assert_called_once_with(demo.engine_url, timeout=expected)
                summary = json.loads((Path(directory) / "operator-summary.json").read_text())
                self.assertEqual(summary["verdict"], "pass")
