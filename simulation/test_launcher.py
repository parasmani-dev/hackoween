"""Launcher admission tests: no simulator or model network calls."""

import contextlib
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from simulation import check_http_fetch as launcher


class LauncherTests(unittest.TestCase):
    base = [str(Path(__file__).resolve().parents[1] / "engine/server.py")]

    def rejected(self, extra, message):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            launcher.parse_args(self.base + extra)
        self.assertEqual(error.exception.code, 2)
        self.assertIn(message, stderr.getvalue())

    def test_offline_default_and_explicit_ports(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(launcher.parse_args(self.base).engine_port, 8080)
            self.assertEqual(launcher.parse_args(self.base + ["--engine-port", "18884"]).engine_port, 18884)
        for field in ("--engine-port", "--bridge-port"):
            for value in ("0", "-1", "65536", "2.5"):
                self.rejected([field, value], field)

    def test_missing_evaluator_rejected_before_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            self.rejected(["--command", "Fetch the round thing", "--max-decisions", "3"],
                          "--expected-fixture-key is required")

    def test_live_requires_budget_and_cannot_run_timing_rehearsal(self):
        command = ["--command", "Fetch the round thing", "--expected-fixture-key", "item_1"]
        self.rejected(command, "--max-decisions is required")
        self.rejected(command + ["--max-decisions", "3", "--measure-timing"],
                      "offline rehearsal only")
        self.rejected(["--command", "   "], "must not be blank")

    def test_preflight_starts_no_adapter_engine_or_model(self):
        with patch.object(launcher.sys, "argv", ["launcher", *self.base, "--preflight-only"]), \
             patch.object(launcher, "WebotsAdapter") as adapter, \
             patch.object(launcher.subprocess, "run") as child, \
             patch.object(launcher.importlib.util, "spec_from_file_location") as engine:
            launcher.main()
        adapter.assert_not_called()
        child.assert_not_called()
        engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
