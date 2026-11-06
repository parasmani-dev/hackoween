"""Live operator admission/lifecycle checks with no model or simulator calls."""

import json
import os
import threading
import time
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from rover_lab.live_operator import LiveConflict, LiveOperator, LiveRequestError


class LiveOperatorTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.live = LiveOperator("http://127.0.0.1:19999", self.root)
        self.live.call = Mock(side_effect=lambda path, payload, **kw: {
            "/run/start": {"state": "Locate"},
            "/run/finish": {"state": "Done", "outcome": "pass", "pov_status": "complete"},
            "/run/reset": {"state": "Idle"},
        }[path])
        self.state = patch("rover_lab.live_operator.EngineClient").start()
        self.state.return_value.state.return_value = {"state": "Idle"}
        self.addCleanup(patch.stopall)

    def data(self, **changes):
        return {"command": "Fetch an object chosen by the operator", "provider": "claude",
                "max_decisions": 12, "expected_fixture_key": "item_4", "confirm_paid": True,
                **changes}

    def run_mock(self, provider="claude", scored=True, fail=False):
        def runner(command, **kwargs):
            folder = kwargs["output_root"] / "run-offline"
            folder.mkdir()
            (folder / "steps.jsonl").write_text(json.dumps({"step": 1, "tool": "turn",
                "args": {"degrees": 45}, "result": {"success": True}, "frame_path": "0000.png"}) + "\n")
            if fail:
                raise RuntimeError("DO-NOT-EXPOSE-PRIVATE-DETAILS")
            return SimpleNamespace(status="engine_done", decisions=1,
                                   usage={"input_tokens": 10, "output_tokens": 2}, run_dir=folder)
        with patch("rover_lab.live_operator.ClaudeClient", return_value=SimpleNamespace(model="offline-claude")), \
             patch("rover_lab.live_operator.OpenAIClient", return_value=SimpleNamespace(model="offline-openai")), \
             patch("rover_lab.live_operator.run_command", side_effect=runner) as run, \
             patch("rover_lab.live_operator.prepare_attempt", return_value={"attempt_id": "offline-attempt"}):
            self.live.start(self.data(provider=provider, expected_fixture_key="item_4" if scored else None))
            self.live._thread.join(timeout=5)
            self.assertFalse(self.live._thread.is_alive())
            return run

    def test_both_providers_run_real_runner_interface_without_scoring_metadata(self):
        for provider in ("claude", "openai"):
            with self.subTest(provider=provider):
                self.live.call.reset_mock()
                run = self.run_mock(provider)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args, ("Fetch an object chosen by the operator",))
                self.assertEqual(set(run.call_args.kwargs), {"provider", "engine", "output_root", "max_decisions", "audit_requests", "local_pickup_checks"})
                self.assertTrue(run.call_args.kwargs["audit_requests"])
                self.assertTrue(run.call_args.kwargs["local_pickup_checks"])
                self.assertEqual(run.call_args.kwargs["provider"], provider)
                self.assertEqual(self.live.call.call_args_list[0].args[1], {
                    "command": "Fetch an object chosen by the operator", "record_pov": True,
                    "expected_fixture_key": "item_4"})
                self.assertEqual(self.live.snapshot()["result"]["verdict"], "pass")
                self.assertEqual(self.live.snapshot()["result"]["execution_profile"], "vision_with_local_approach_control")
                self.assertEqual(json.loads((self.live.active / "provenance.json").read_text())["execution_profile"], "vision_with_local_approach_control")
                self.assertEqual(json.loads((self.live.active / "operator-summary.json").read_text())["expected_fixture_key"], "item_4")
                self.assertNotIn("expected_fixture_key", self.live.snapshot()["result"])
                self.assertFalse(self.live.running)

    def test_unscored_command_is_not_reported_as_pass(self):
        self.run_mock(scored=False)
        self.assertNotIn("expected_fixture_key", self.live.call.call_args_list[0].args[1])
        self.assertEqual(self.live.snapshot()["result"]["verdict"], "unscored")

    def test_invalid_values_rejected_before_provider_or_engine_mutation(self):
        cases = ({"command": " "}, {"command": "x" * 501}, {"max_decisions": True},
                 {"max_decisions": 0}, {"max_decisions": 61}, {"max_decisions": "12"},
                 {"provider": "unknown"}, {"expected_fixture_key": []},
                 {"expected_fixture_key": "item_99"}, {"confirm_paid": False}, {"api_key": "never-accept"})
        with patch("rover_lab.live_operator.ClaudeClient") as client:
            for case in cases:
                with self.subTest(case=case), self.assertRaises(LiveRequestError):
                    self.live.start(self.data(**case))
            client.assert_not_called()
            self.live.call.assert_not_called()

    def test_missing_key_does_not_start_or_spend(self):
        with patch("rover_lab.live_operator.ClaudeClient", side_effect=RuntimeError("secret-key")), \
             patch("rover_lab.live_operator.run_command") as runner:
            with self.assertRaisesRegex(LiveRequestError, "Provider setup is incomplete"):
                self.live.start(self.data())
            self.live.call.assert_not_called()
            runner.assert_not_called()
            self.assertFalse(self.live.running)

    def test_concurrent_start_and_reset_blocked_until_finish(self):
        entered, release = threading.Event(), threading.Event()
        def runner(command, **kwargs):
            entered.set()
            release.wait(3)
            folder = kwargs["output_root"] / "run-offline"
            folder.mkdir()
            return SimpleNamespace(status="decision_limit", decisions=12, usage={}, run_dir=folder)
        with patch("rover_lab.live_operator.ClaudeClient", return_value=SimpleNamespace(model="offline")), \
             patch("rover_lab.live_operator.prepare_attempt", return_value={}), \
             patch("rover_lab.live_operator.run_command", side_effect=runner):
            self.live.start(self.data())
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(LiveConflict): self.live.start(self.data())
                with self.assertRaises(LiveConflict): self.live.reset()
                self.assertTrue(self.live.snapshot()["running"])
            finally:
                release.set()
                self.live._thread.join(5)
            self.assertFalse(self.live.running)
            self.assertEqual(self.live.call.call_args_list[-1].args[1], {"reason": "decision_limit"})
            self.assertEqual(self.live.reset(), {"state": "Idle"})

    def test_failure_finalizes_once_and_never_exposes_raw_error(self):
        self.run_mock(fail=True)
        self.assertEqual([call.args[0] for call in self.live.call.call_args_list], ["/run/start", "/run/finish"])
        public = json.dumps(self.live.snapshot())
        self.assertNotIn("DO-NOT-EXPOSE", public)
        self.assertIn("stopped", public)
        self.assertFalse(self.live.running)

    def test_status_and_reset_never_invoke_provider(self):
        with patch("rover_lab.live_operator.ClaudeClient") as client, \
             patch("rover_lab.live_operator.run_command") as runner, \
             patch("agent.claude.request") as claude_api, \
             patch("agent.openai_client.request") as openai_api:
            self.assertFalse(self.live.snapshot()["running"])
            self.live.reset()
            client.assert_not_called()
            runner.assert_not_called()
            claude_api.assert_not_called()
            openai_api.assert_not_called()

    def test_private_operator_fields_and_credentials_are_not_in_status(self):
        self.live.result = {"provider": "claude", "model": "offline-model", "usage": {},
                            "api_key": "never-show-key", "expected_fixture_key": "item_4",
                            "pov_manifest": "private-path", "scored": True}
        public = json.dumps(self.live.snapshot())
        for forbidden in ("never-show-key", "api_key", "expected_fixture_key", "item_4", "private-path", "pov_manifest"):
            self.assertNotIn(forbidden, public)

    def test_other_active_engine_run_is_preserved(self):
        self.state.return_value.state.return_value = {"state": "Carry"}
        with self.assertRaises(LiveConflict): self.live.start(self.data())
        with self.assertRaises(LiveConflict): self.live.reset()
        self.live.call.assert_not_called()

    def test_live_source_counts_use_only_complete_decision_source_rows(self):
        attempt = self.root / "attempt-counts"
        folder = attempt / "run-counts"
        folder.mkdir(parents=True)
        self.live.active = attempt
        self.live.running = True
        rows = [{"step": i, "tool": "pickup", "args": {}, "result": {"success": False}, "frame_path": "0000.png"} for i in (1, 2, 3, 4)]
        (folder / "steps.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        sources = [{"step": 1, "source": "model", "request_index": 1},
                   {"step": 2, "source": "local_pickup_check", "request_index": None},
                   {"step": 3, "source": "local_approach_probe", "request_index": None},
                   {"step": 4, "source": "model", "request_index": 2}]
        (folder / "decision_sources.jsonl").write_text("".join(json.dumps(row) + "\n" for row in sources) + '{"step":5,"source":"model"}')
        snapshot = self.live.snapshot()
        self.assertEqual(snapshot["decisions"], 4)
        self.assertEqual(snapshot["model_requests"], 2)
        self.assertEqual(snapshot["local_pickup_checks"], 1)
        self.assertEqual(snapshot["local_approach_probes"], 1)
        self.assertEqual(snapshot["local_control_steps"], 2)
        self.assertEqual(snapshot["execution_profile"], "vision_with_local_approach_control")
        self.assertNotIn("request_index", snapshot)

    def test_completed_run_keeps_distinct_local_probe_and_pickup_counts(self):
        def runner(command, **kwargs):
            folder = kwargs["output_root"] / "run-source-counts"
            folder.mkdir()
            sources = [{"step": i + 1, "source": source, "request_index": request}
                       for i, (source, request) in enumerate((("model", 1),
                            ("local_pickup_check", None), ("local_approach_probe", None), ("model", 2)))]
            (folder / "decision_sources.jsonl").write_text("".join(json.dumps(row) + "\n" for row in sources))
            return SimpleNamespace(status="engine_done", decisions=4, model_requests=2, usage={}, run_dir=folder)
        with patch("rover_lab.live_operator.ClaudeClient", return_value=SimpleNamespace(model="offline")), \
             patch("rover_lab.live_operator.prepare_attempt", return_value={}), \
             patch("rover_lab.live_operator.run_command", side_effect=runner):
            self.live.start(self.data())
            self.live._thread.join(5)
        record = json.loads((self.live.active / "operator-summary.json").read_text())
        self.assertEqual(record["decisions"], 4)
        self.assertEqual(record["model_requests"], 2)
        self.assertEqual(record["local_pickup_checks"], 1)
        self.assertEqual(record["local_approach_probes"], 1)
        self.assertEqual(record["local_control_steps"], 2)
        self.assertEqual(self.live.snapshot()["decisions"], 4)


class ScenarioTwoLiveRoutesTests(unittest.TestCase):
    def setUp(self):
        from rover_lab import dashboard
        self.dashboard = dashboard
        self.one, self.two = MagicMock(), MagicMock()
        self.two.running = False
        self.two.engine_url = "http://127.0.0.1:19981"
        self.two.active = None
        self.two.snapshot.return_value = {"running": False, "rows": [], "result": None}
        self.demo = MagicMock()
        self.demo.running = False
        self.demo.engine_url = self.two.engine_url
        with patch.object(dashboard, "Demo", return_value=self.demo), \
             patch.object(dashboard, "LiveOperator", side_effect=[self.one, self.two]), \
             patch.object(dashboard.secrets, "token_urlsafe", return_value="offline-jury-token"), \
             patch.object(dashboard, "ThreadingHTTPServer") as server:
            dashboard.serve(19980, 19981)
            self.handler_class = server.call_args.args[1]

    def handler(self, path, data=None, origin="http://127.0.0.1:19980", token="offline-jury-token"):
        handler = object.__new__(self.handler_class)
        handler.path = path
        body = json.dumps(data).encode() if data is not None else b""
        handler.headers = {"Origin": origin, "X-Jury-Token": token, "Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        handler.reply = Mock()
        return handler

    def test_live_custom_route_uses_scenario_two_operator(self):
        body = {"command": "Fetch the yellow cone", "provider": "claude", "max_decisions": 60,
                "expected_fixture_key": "item_5", "confirm_paid": True}
        handler = self.handler("/scenario-2/live/start", body)
        handler.do_POST()
        self.two.start.assert_called_once_with(body)
        self.one.start.assert_not_called()

    def test_jury_token_and_origin_are_required_before_live_start(self):
        for origin, token in (("https://unrelated.example", "offline-jury-token"),
                              ("http://127.0.0.1:19980", "invalid-token")):
            with self.subTest(origin=origin, token=token):
                handler = self.handler("/scenario-2/live/start", {}, origin, token)
                handler.do_POST()
                self.assertEqual(handler.reply.call_args.kwargs["status"], 403)
        self.two.start.assert_not_called()

    def test_scripted_actions_and_layout_cannot_interrupt_live_run(self):
        self.two.running = True
        for path, body in (("/select", {"layout": "B"}), ("/start", {})):
            handler = self.handler(path, body)
            handler.do_POST()
            self.assertEqual(handler.reply.call_args.kwargs["status"], 409)
        self.demo.select.assert_not_called()

    def test_scripted_run_blocks_live_start(self):
        self.demo.running = True
        handler = self.handler("/scenario-2/live/start", {})
        handler.do_POST()
        self.assertEqual(handler.reply.call_args.kwargs["status"], 409)
        self.two.start.assert_not_called()

    def test_live_status_is_authenticated_and_never_starts_provider(self):
        handler = self.handler("/scenario-2/live/status", token="invalid")
        handler.do_GET()
        self.assertEqual(handler.reply.call_args.kwargs["status"], 403)
        self.two.snapshot.assert_not_called()
        with patch.object(self.dashboard, "EngineClient") as engine, \
             patch("agent.claude.request") as claude_api, patch("agent.openai_client.request") as openai_api:
            engine.return_value.state.return_value = {"state": "Idle"}
            handler = self.handler("/scenario-2/live/status")
            handler.do_GET()
            self.two.snapshot.assert_called_once()
            self.two.start.assert_not_called()
            claude_api.assert_not_called()
            openai_api.assert_not_called()

    def test_watch_never_switches_to_live_custom_status_or_camera(self):
        self.two.running = True
        with patch.object(self.dashboard, "watch_snapshot", return_value={"label": "Approved scripted replay"}) as snapshot:
            handler = self.handler("/watch/status", token="")
            handler.do_GET()
            snapshot.assert_called_once_with(self.demo)
            self.two.snapshot.assert_not_called()
        handler = self.handler("/watch/front", token="")
        handler.do_GET()
        self.assertEqual(handler.reply.call_args.kwargs["status"], 503)

    def test_live_thinking_keeps_old_frames_with_fresh_bridge_heartbeat(self):
        self.two.running = True
        now = time.time()
        with TemporaryDirectory() as folder:
            front = Path(folder) / "front.jpg"
            spectator = Path(folder) / "spectator.jpg"
            for path in (front, spectator):
                path.write_bytes(b"offline-jpeg-fixture")
                os.utime(path, (now - 30, now - 30))
            Path(str(front) + ".json").write_text(json.dumps({"ready": True, "heartbeat_ms": now * 1000}))
            with patch.dict(os.environ, {"ROVER_PREVIEW_FILE": str(front), "ROVER_SPECTATOR_FILE": str(spectator)}):
                for name in ("front", "spectator"):
                    with self.subTest(camera=name):
                        handler = self.handler("/" + name)
                        handler.do_GET()
                        self.assertEqual(handler.reply.call_args.kwargs.get("status", 200), 200)
                        self.assertEqual(handler.reply.call_args.kwargs["frame_state"], "waiting")
                        self.assertEqual(handler.reply.call_args.args[0], b"offline-jpeg-fixture")

    def test_stale_bridge_rejects_both_cameras_even_with_recent_images(self):
        self.two.running = True
        now = time.time()
        with TemporaryDirectory() as folder:
            front = Path(folder) / "front.jpg"
            spectator = Path(folder) / "spectator.jpg"
            for path in (front, spectator):
                path.write_bytes(b"offline-jpeg-fixture")
            Path(str(front) + ".json").write_text(json.dumps({"ready": True, "heartbeat_ms": (now - 10) * 1000}))
            with patch.dict(os.environ, {"ROVER_PREVIEW_FILE": str(front), "ROVER_SPECTATOR_FILE": str(spectator)}):
                for name in ("front", "spectator"):
                    with self.subTest(camera=name):
                        handler = self.handler("/" + name)
                        handler.do_GET()
                        self.assertEqual(handler.reply.call_args.kwargs["status"], 503)

    def test_idle_old_frame_is_paused_when_bridge_is_alive(self):
        now = time.time()
        with TemporaryDirectory() as folder:
            front = Path(folder) / "front.jpg"
            spectator = Path(folder) / "spectator.jpg"
            for path in (front, spectator):
                path.write_bytes(b"offline-jpeg-fixture")
                os.utime(path, (now - 30, now - 30))
            Path(str(front) + ".json").write_text(json.dumps({"ready": True, "heartbeat_ms": now * 1000}))
            with patch.dict(os.environ, {"ROVER_PREVIEW_FILE": str(front), "ROVER_SPECTATOR_FILE": str(spectator)}):
                for name in ("front", "spectator"):
                    handler = self.handler("/" + name)
                    handler.do_GET()
                    self.assertEqual(handler.reply.call_args.kwargs["frame_state"], "paused")


if __name__ == "__main__":
    unittest.main()
