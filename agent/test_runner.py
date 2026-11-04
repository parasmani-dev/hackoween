"""Phase 2 loop checks with mocked clients and real PNG/JSONL artifacts."""

import base64
import json
import os
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from PIL import Image

from agent.claude import ClaudeError, Decision
from agent.engine import ActionOutcomeUncertain
from agent.runner import run_command


def jpeg(color):
    with BytesIO() as stream:
        Image.new("RGB", (640, 480), color).save(
            stream, format="JPEG", comment=b"private-metadata"
        )
        return stream.getvalue()


def state(name="Locate", retry_count=0):
    return dict(state=name, x=0, y=0, heading=0, holding=False, retry_count=retry_count)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Mock()
        self.engine.state.return_value = state()
        self.engine.look.return_value = jpeg("red")
        self.engine.execute.return_value = {"success": True}
        self.claude = Mock()
        self.claude.decide.return_value = Decision("pickup", {}, {"input_tokens": 2, "output_tokens": 1})

    def run_rover(self, **kwargs):
        return run_command(
            "Fetch the red object", engine=self.engine, claude=self.claude,
            output_root=self.temp.name, **kwargs,
        )

    def records(self, result):
        return [
            json.loads(line)
            for line in (result.run_dir / "steps.jsonl").read_text(encoding="utf-8").splitlines()
        ]

    def test_camera_refresh_reuse_and_artifacts(self):
        self.engine.look.side_effect = [jpeg("red"), jpeg("green"), jpeg("blue")]
        self.engine.state.side_effect = [
            state(), state("Blocked", 1), state(), state("Carry"), state("Done"),
        ]
        self.engine.execute.side_effect = [
            {"success": False, "collided": True}, {"success": True},
            {"success": True}, {"success": True, "in_zone": True},
        ]
        actions = [("move", {"distance": 0.5}), ("turn", {"degrees": 45}), ("pickup", {}), ("drop", {})]
        self.claude.decide.side_effect = [
            Decision(name, args, {"input_tokens": 2, "output_tokens": 1, "cache_read_input_tokens": 3})
            for name, args in actions
        ]
        result = self.run_rover()
        self.assertEqual(result.status, "engine_done")
        self.assertEqual(result.decisions, 4)
        self.assertEqual(result.usage, {"input_tokens": 8, "output_tokens": 4, "cache_read_input_tokens": 12})
        self.assertEqual(self.engine.look.call_count, 3)
        self.assertEqual([call.args for call in self.engine.execute.call_args_list], actions)
        rows = self.records(result)
        self.assertEqual([row["frame_path"] for row in rows], ["0000.png", "0001.png", "0002.png", "0002.png"])
        for row, call in zip(rows, self.claude.decide.call_args_list, strict=True):
            blocks = call.args[0][0]["content"]
            self.assertEqual([block["type"] for block in blocks], ["text", "image"])
            frame = base64.b64decode(blocks[1]["source"]["data"])
            self.assertEqual(frame, (result.run_dir / row["frame_path"]).read_bytes())
            self.assertNotIn(b"private-metadata", frame)
            with Image.open(BytesIO(frame)) as image:
                self.assertEqual((image.format, image.size), ("PNG", (640, 480)))

    def test_decision_cap_compact_history_and_unique_runs(self):
        first = self.run_rover()
        self.assertEqual((first.status, first.decisions), ("decision_limit", 60))
        self.assertEqual(self.engine.execute.call_count, 60)
        self.assertEqual(self.engine.look.call_count, 1)
        context = json.loads(self.claude.decide.call_args.args[0][0]["content"][0]["text"])
        self.assertEqual(len(context["recent_actions"]), 8)
        second = self.run_rover(max_decisions=1)
        self.assertNotEqual(first.run_dir, second.run_dir)
        self.assertEqual(len(self.records(first)), 60)

    def test_uncertain_action_is_logged_once(self):
        self.engine.execute.side_effect = ActionOutcomeUncertain("private exception detail")
        result = self.run_rover()
        self.assertEqual(result.status, "action_uncertain")
        self.engine.execute.assert_called_once()
        self.assertEqual(self.records(result)[0]["result"], {"outcome": "uncertain"})

    def test_exhausted_recovery_stops_before_next_decision(self):
        self.engine.state.side_effect = [state("Blocked", 2), state("Blocked", 3)]
        result = self.run_rover()
        self.assertEqual((result.status, result.decisions), ("recovery_exhausted", 1))
        self.engine.execute.assert_called_once()

    def test_bad_camera_and_failed_claude_do_not_dispatch(self):
        self.claude.decide.side_effect = ClaudeError("private exception detail")
        self.assertEqual(self.run_rover().status, "claude_error")
        self.engine.look.return_value = b"bad frame"
        self.assertEqual(self.run_rover().status, "engine_error")
        self.engine.execute.assert_not_called()

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "mock-key", "CLAUDE_MODEL": "mock-model"})
    @patch("agent.claude.request")
    def test_request_audit_matches_outbound_body_without_headers(self, request):
        request.return_value = ("application/json", json.dumps({
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "name": "pickup", "input": {}}],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }).encode())
        result = run_command("Fetch the red object", engine=self.engine,
                             output_root=self.temp.name, max_decisions=1, audit_requests=True)
        audit = (result.run_dir / "claude_requests.jsonl").read_text(encoding="utf-8")
        self.assertEqual(json.loads(audit), request.call_args.kwargs["payload"])
        self.assertNotIn("mock-key", audit)
        self.assertNotIn("x-api-key", audit)
        self.assertEqual(result.status, "decision_limit")
