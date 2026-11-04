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

    def test_alternating_turns_trigger_progress_guidance_until_rover_moves(self):
        self.claude.decide.side_effect = [
            Decision("turn", {"degrees": angle}, {}) for angle in (10, -10, 10)
        ] + [Decision("move", {"distance": 0.2}, {}), Decision("pickup", {}, {})]
        result = self.run_rover(max_decisions=5)
        prompts = [call.kwargs["system"] for call in self.claude.decide.call_args_list]
        self.assertTrue(all("Stop fine alignment" not in prompt for prompt in prompts[:3]))
        self.assertIn("Stop fine alignment", prompts[3])
        self.assertIn("If the current image shows clear floor", prompts[3])
        self.assertEqual(prompts[0], prompts[4])
        self.assertEqual([row["tool"] for row in self.records(result)],
                         ["turn", "turn", "turn", "move", "pickup"])

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

    def test_repeated_reversal_is_not_dispatched(self):
        self.claude.decide.side_effect = [
            Decision("turn", {"degrees": angle}, {"input_tokens": 1})
            for angle in (-15, 10, -10, 15, 15)
        ] + [Decision("move", {"distance": 0.2}, {"input_tokens": 1})]
        result = self.run_rover(max_decisions=6)
        self.assertEqual(result.decisions, 6)
        self.assertEqual(result.usage["input_tokens"], 6)
        rows = self.records(result)
        self.assertEqual([row["step"] for row in rows], list(range(1, 7)))
        for row in rows[3:5]:
            self.assertEqual(row["result"], {"outcome": "not_dispatched", "reason": "navigation_guard"})
            self.assertEqual(row["frame_path"], rows[5]["frame_path"])
        self.assertEqual(self.engine.execute.call_count, 4)
        self.assertEqual(self.engine.look.call_count, 5)
        calls = self.claude.decide.call_args_list
        self.assertIn("rejected before dispatch", calls[4].kwargs["system"])
        frames = [call.args[0][0]["content"][1] for call in calls[3:]]
        self.assertEqual(frames, [frames[0]] * 3)
        self.engine.execute.assert_called_with("move", {"distance": 0.2})

    def test_repeated_rejections_stop_early(self):
        self.claude.decide.side_effect = [
            Decision("turn", {"degrees": angle}, {})
            for angle in (-15, 10, -10, 15, 15, 15)
        ]
        result = self.run_rover()
        self.assertEqual((result.status, result.decisions), ("navigation_stalled", 6))
        self.assertEqual(self.engine.execute.call_count, 3)

    def test_same_direction_scan_is_bounded(self):
        self.claude.decide.return_value = Decision("turn", {"degrees": 45}, {})
        result = self.run_rover()
        self.assertEqual((result.status, result.decisions), ("navigation_stalled", 15))
        self.assertEqual(self.engine.execute.call_count, 12)

    def test_audit_reconciles_frames_and_rejected_turn_for_both_providers(self):
        for provider, module in (("claude", "claude"), ("openai", "openai_client")):
            with self.subTest(provider=provider):
                responses = []
                for degrees in (-15, 10, -10, 15):
                    args = {"degrees": degrees}
                    if provider == "claude":
                        body = {
                            "stop_reason": "tool_use",
                            "content": [{"type": "tool_use", "name": "turn", "input": args}],
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                        }
                    else:
                        body = {
                            "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
                                {"type": "function", "function": {"name": "turn",
                                                                 "arguments": json.dumps(args)}}
                            ]}}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        }
                    responses.append(("application/json", json.dumps(body).encode()))
                self.engine.reset_mock()
                self.engine.look.side_effect = [jpeg(color) for color in ("red", "blue", "white", "black")]
                command = "Fetch the red sphere and drop it in the marked zone"
                with patch.dict(os.environ, {
                    "ANTHROPIC_API_KEY": "offline-test-key", "CLAUDE_MODEL": "offline-claude",
                    "OPENAI_API_KEY": "offline-test-key", "OPENAI_MODEL": "offline-openai",
                }), patch(f"agent.{module}.request", side_effect=responses) as request:
                    result = run_command(command, engine=self.engine, provider=provider,
                                         output_root=self.temp.name, max_decisions=4, audit_requests=True)
                rows = self.records(result)
                audit = (result.run_dir / f"{provider}_requests.jsonl").read_text(encoding="utf-8")
                payloads = [json.loads(line) for line in audit.splitlines()]
                self.assertEqual(result.decisions, 4)
                self.assertEqual(payloads, [call.kwargs["payload"] for call in request.call_args_list])
                self.assertEqual(len(rows), 4)
                self.assertEqual(self.engine.execute.call_count, 3)
                self.assertEqual(rows[-1]["result"]["outcome"], "not_dispatched")
                self.assertNotIn("offline-test-key", audit)
                for row, payload in zip(rows, payloads, strict=True):
                    if provider == "claude":
                        blocks = payload["messages"][0]["content"]
                        encoded = blocks[1]["source"]["data"]
                        names = {tool["name"] for tool in payload["tools"]}
                    else:
                        blocks = payload["messages"][1]["content"]
                        encoded = blocks[1]["image_url"]["url"].split(",", 1)[1]
                        names = {tool["function"]["name"] for tool in payload["tools"]}
                    context = json.loads(blocks[0]["text"])
                    self.assertEqual(set(context), {"command", "state", "remaining_decisions", "recent_actions"})
                    self.assertEqual(context["command"], command)
                    self.assertEqual(set(context["state"]), {"state", "x", "y", "heading", "holding", "retry_count"})
                    self.assertEqual(names, {"move", "turn", "pickup", "drop"})
                    self.assertEqual(base64.b64decode(encoded), (result.run_dir / row["frame_path"]).read_bytes())
                    for action in context["recent_actions"]:
                        self.assertEqual(set(action), {"tool", "args", "result"})
                        self.assertEqual(set(action["args"]), {"degrees"})
                        self.assertEqual(set(action["result"]), {"success"})

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
        self.engine.state.side_effect = [state("Blocked", 2), state("Done", 3)]
        result = self.run_rover()
        self.assertEqual((result.status, result.decisions), ("recovery_exhausted", 1))
        self.engine.execute.assert_called_once()

    def test_bad_camera_and_failed_claude_do_not_dispatch(self):
        self.claude.decide.side_effect = ClaudeError("private exception detail")
        result = self.run_rover()
        self.assertEqual(result.status, "claude_error")
        self.assertEqual(self.records(result)[0]["result"],
                         {"outcome": "not_dispatched", "reason": "provider_error"})
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
        result = run_command("Fetch the red object and drop it in the drop zone", engine=self.engine,
                             output_root=self.temp.name, max_decisions=1, audit_requests=True)
        audit = (result.run_dir / "claude_requests.jsonl").read_text(encoding="utf-8")
        payload = json.loads(audit)
        self.assertEqual(payload, request.call_args.kwargs["payload"])
        self.assertNotIn("green", payload["system"].lower())
        self.assertNotIn("mock-key", audit)
        self.assertNotIn("x-api-key", audit)
        self.assertEqual(result.status, "decision_limit")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "mock-openai-key", "OPENAI_MODEL": "gpt-5-mini"})
    @patch("agent.openai_client.request")
    def test_openai_run_dispatches_one_validated_action(self, request):
        call = {"type": "function", "function": {"name": "move", "arguments": '{"distance":-0.5}'}}
        response = {
            "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [call]}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8,
                      "prompt_tokens_details": {"cached_tokens": 5}},
        }
        request.return_value = ("application/json", json.dumps(response).encode())
        options = dict(engine=self.engine, output_root=self.temp.name, provider="openai",
                       max_decisions=1, audit_requests=True)
        result = run_command("Fetch the red object and drop it in the drop zone", **options)
        self.engine.execute.assert_called_once_with("move", {"distance": -0.5})
        self.assertEqual(result.usage, {"input_tokens": 15, "cache_read_input_tokens": 5, "output_tokens": 8})
        audit = (result.run_dir / "openai_requests.jsonl").read_text(encoding="utf-8")
        payload = json.loads(audit)
        self.assertEqual(payload, request.call_args.kwargs["payload"])
        self.assertNotIn("green", payload["messages"][0]["content"].lower())
        self.assertNotIn("mock-openai-key", audit)
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertEqual(payload["messages"][1]["content"][1]["type"], "image_url")
        self.assertTrue(all(tool["function"]["strict"] for tool in payload["tools"]))

        response["choices"][0]["message"]["tool_calls"].append(call)
        request.return_value = ("application/json", json.dumps(response).encode())
        self.assertEqual(run_command("Fetch the red object", **options).status, "openai_error")
        self.engine.execute.assert_called_once()
