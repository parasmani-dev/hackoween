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
    def test_range_guard_rejects_contact_move_then_allows_direct_pickup(self):
        self.engine.depth.return_value = 0.07
        self.engine.state.side_effect = [state(), {**state('Carry'), 'holding': True}]
        self.claude.decide.side_effect = [Decision('move', {'distance': 0.2}, {}),
                                        Decision('pickup', {}, {})]
        result = self.run_rover(max_decisions=2)
        rows = self.records(result)
        self.assertEqual(rows[0]['result']['outcome'], 'not_dispatched')
        self.engine.execute.assert_called_once_with('pickup', {})
        self.assertEqual(self.engine.look.call_count, 1)
        self.assertEqual(rows[0]['frame_path'], rows[1]['frame_path'])

    def test_range_guard_allows_short_move_with_clearance(self):
        self.engine.depth.return_value = 0.10
        self.claude.decide.return_value = Decision('move', {'distance': 0.05}, {})
        result = self.run_rover(max_decisions=1)
        self.engine.execute.assert_called_once_with('move', {'distance': 0.05})
        self.assertEqual(self.records(result)[0]['result'], {'success': True})

    def test_visual_memory_survives_failed_pickup_and_clears_on_success(self):
        self.claude.decide.side_effect = [
            Decision('pickup', {}, {}, 'Red sphere centered, lower edge clipped.'),
            Decision('move', {'distance': 0.03}, {}),
            Decision('pickup', {}, {}), Decision('turn', {'degrees': 20}, {})]
        self.engine.execute.side_effect = [{'success': False}, {'success': True},
                                           {'success': True}, {'success': True}]
        self.engine.state.side_effect = [state(), state(), {**state(), 'x': .03},
                                         {**state('Carry'), 'holding': True}, state()]
        self.run_rover(max_decisions=4)
        contexts = [json.loads(call.args[0][0]['content'][0]['text'])
                    for call in self.claude.decide.call_args_list]
        self.assertEqual(contexts[1]['previous_visual_observation'],
                         'Red sphere centered, lower edge clipped.')
        self.assertEqual(contexts[2]['previous_visual_observation'],
                         contexts[1]['previous_visual_observation'])
        self.assertIn('drop zone', contexts[3]['previous_visual_observation'])
        self.engine.execute.assert_any_call('pickup', {})

    def test_controller_records_proposal_and_executes_one_corrected_action(self):
        self.claude.decide.return_value = Decision('pickup', {}, {}, 'Target on left',
            {'box': [70, 250, 230, 430], 'path_clear': True, 'floor_zone': False})
        result = self.run_rover(max_decisions=1)
        row = self.records(result)[0]
        self.assertEqual(row['proposal']['tool'], 'pickup')
        self.assertEqual(row['tool'], 'turn')
        self.assertEqual(row['controller_correction'], 'align_to_reported_target')
        self.assertEqual(self.engine.execute.call_count, 1)
        self.assertEqual(self.engine.look.call_count, 2)

    def test_carry_collision_survives_reverse_until_heading_changes(self):
        carrying = {**state('Carry'), 'holding': True}
        self.engine.state.return_value = carrying
        self.engine.execute.side_effect = [{'success': False, 'collided': True},
                                           {'success': True, 'collided': False}]
        self.claude.decide.side_effect = [Decision('move', {'distance': .2}, {}),
            Decision('move', {'distance': -.3}, {}), Decision('move', {'distance': .2}, {})]
        result = self.run_rover(max_decisions=3)
        self.assertEqual(self.engine.execute.call_count, 2)
        self.assertEqual(self.records(result)[2]['result']['outcome'], 'not_dispatched')

    def test_failed_turn_cannot_be_repeated_in_same_direction(self):
        self.engine.execute.return_value = {'success': False}
        self.claude.decide.side_effect = [Decision('turn', {'degrees': -12}, {}),
                                         Decision('turn', {'degrees': -12}, {})]
        result = self.run_rover(max_decisions=2)
        self.engine.execute.assert_called_once()
        self.assertEqual(self.records(result)[1]['result']['outcome'], 'not_dispatched')

    def test_empty_drop_is_unavailable_and_release_clears_memory(self):
        self.engine.state.side_effect = [{**state('Carry'), 'holding': True}, state(),
                                         {**state('Carry'), 'holding': True}]
        self.engine.execute.side_effect = [{'success': True, 'in_zone': False}, {'success': True}]
        self.claude.decide.side_effect = [Decision('drop', {}, {}, 'Still holding red sphere'),
                                         Decision('pickup', {}, {})]
        self.run_rover(max_decisions=2)
        offered=[call.args[0] for call in self.claude.set_available_actions.call_args_list]
        self.assertNotIn('pickup', offered[0]);self.assertNotIn('drop', offered[1])
        context=json.loads(self.claude.decide.call_args_list[1].args[0][0]['content'][0]['text'])
        self.assertIn('holding is false',context['previous_visual_observation'])

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

    def test_collision_direction_is_rejected_without_mutation_or_fresh_image(self):
        self.engine.execute.side_effect = [{"success": False, "collided": True},
                                           {"success": True, "collided": False}]
        self.claude.decide.side_effect = [Decision('move', {'distance': 0.5}, {}),
                                        Decision('move', {'distance': 0.1}, {}),
                                        Decision('move', {'distance': -0.3}, {})]
        result = self.run_rover(max_decisions=3)
        rows = self.records(result)
        self.assertEqual(rows[1]['result'], {'outcome': 'not_dispatched', 'reason': 'navigation_guard'})
        self.assertEqual(rows[1]['frame_path'], rows[2]['frame_path'])
        self.assertEqual(self.engine.execute.call_count, 2)
        self.assertEqual(self.engine.look.call_count, 3)
        self.assertIn('move(-0.3)', self.claude.decide.call_args_list[1].kwargs['system'])

    def test_reverse_collision_requires_forward_clearance(self):
        self.engine.execute.side_effect = [{'success': False, 'collided': True},
                                           {'success': True, 'collided': False}]
        self.claude.decide.side_effect = [Decision('move', {'distance': -0.5}, {}),
                                        Decision('move', {'distance': -0.1}, {}),
                                        Decision('move', {'distance': 0.3}, {})]
        result = self.run_rover(max_decisions=3)
        self.assertEqual(self.records(result)[1]['result']['reason'], 'navigation_guard')
        self.assertIn('move(0.3)', self.claude.decide.call_args_list[1].kwargs['system'])

    def test_tiny_final_approach_is_allowed_after_automatic_collision_backoff(self):
        for sign in (-1, 1):
            with self.subTest(sign=sign):
                self.engine.reset_mock()
                self.engine.execute.side_effect = [{'success': False, 'collided': True},
                                                   {'success': True, 'collided': False}]
                self.claude.decide.side_effect = [Decision('move', {'distance': sign * 0.1}, {}),
                                                Decision('move', {'distance': sign * 0.05}, {})]
                result = self.run_rover(max_decisions=2)
                self.assertEqual(self.engine.execute.call_count, 2)
                self.assertTrue(self.records(result)[1]['result']['success'])

    def test_collision_guard_allows_a_new_heading_and_pickup_guidance_reuses_frame(self):
        self.engine.execute.side_effect = [{'success': False, 'collided': True},
            {'success': True}, {'success': True, 'collided': False},
            {'success': False}, {'success': True}]
        self.claude.decide.side_effect = [Decision('move', {'distance': 0.4}, {}),
            Decision('turn', {'degrees': 20}, {}), Decision('move', {'distance': 0.2}, {}),
            Decision('pickup', {}, {}), Decision('pickup', {}, {})]
        result = self.run_rover(max_decisions=5)
        rows = self.records(result)
        self.assertEqual(self.engine.execute.call_count, 4)
        self.assertEqual(rows[3]['frame_path'], rows[4]['frame_path'])
        self.assertEqual(rows[4]['result']['outcome'], 'not_dispatched')
        prompt = self.claude.decide.call_args_list[4].kwargs['system']
        self.assertIn('Pickup failed', prompt)
        self.assertIn('Do not repeat pickup', prompt)

    def test_failed_pickup_and_drop_are_dispatched_once_per_unchanged_pose(self):
        for tool in ('pickup', 'drop'):
            for provider in ('claude', 'openai'):
                with self.subTest(tool=tool, provider=provider):
                    self.engine.reset_mock()
                    self.claude.reset_mock()
                    self.engine.execute.return_value = {'success': False}
                    self.engine.state.return_value = {**state(), 'holding': tool == 'drop'}
                    self.claude.decide.return_value = Decision(tool, {}, {'input_tokens': 1})
                    result = self.run_rover(provider=provider)
                    self.assertEqual((result.status, result.decisions), ('navigation_stalled', 4))
                    self.engine.execute.assert_called_once_with(tool, {})
                    self.engine.look.assert_called_once()
                    rows = self.records(result)
                    self.assertEqual(len(rows), 4)
                    self.assertTrue(all(row['frame_path'] == '0000.png' for row in rows))
                    self.assertTrue(all(row['result'] == {'outcome': 'not_dispatched',
                        'reason': 'navigation_guard'} for row in rows[1:]))
                    self.assertEqual(result.usage['input_tokens'], 4)
                    self.assertIn('Actions currently unavailable', self.claude.decide.call_args.kwargs['system'])

    def test_zero_or_ineffective_motion_does_not_enable_another_pickup(self):
        self.engine.execute.side_effect = [
            {'success': False}, {'success': True}, {'success': True, 'collided': False}]
        self.claude.decide.side_effect = [Decision('pickup', {}, {}),
            Decision('turn', {'degrees': 0}, {}), Decision('pickup', {}, {}),
            Decision('move', {'distance': 0.1}, {}), Decision('pickup', {}, {})]
        result = self.run_rover(max_decisions=5)
        rows = self.records(result)
        self.assertEqual(self.engine.execute.call_count, 3)
        self.assertEqual(rows[2]['result']['outcome'], 'not_dispatched')
        self.assertEqual(rows[4]['result']['outcome'], 'not_dispatched')
        self.assertEqual(rows[0]['frame_path'], rows[1]['frame_path'])
        self.assertEqual(rows[1]['frame_path'], '0000.png')
        self.assertEqual(rows[2]['frame_path'], '0002.png')
        self.assertEqual(rows[4]['frame_path'], '0004.png')

    def test_pose_correction_enables_pickup_and_clears_old_restriction(self):
        moved = {**state(), 'x': 0.1}
        self.engine.state.side_effect = [state(), state(), moved, moved, state(), state()]
        self.engine.execute.side_effect = [{'success': False}, {'success': True, 'collided': False},
            {'success': True}, {'success': True, 'collided': False}, {'success': True}]
        self.claude.decide.side_effect = [Decision('pickup', {}, {}),
            Decision('move', {'distance': 0.1}, {}), Decision('pickup', {}, {}),
            Decision('move', {'distance': -0.1}, {}), Decision('pickup', {}, {})]
        result = self.run_rover(max_decisions=5)
        self.assertEqual(self.engine.execute.call_count, 5)
        rows = self.records(result)
        self.assertEqual([row['frame_path'] for row in rows],
                         ['0000.png', '0000.png', '0002.png', '0002.png', '0004.png'])
        offered = [call.args[0] for call in self.claude.set_available_actions.call_args_list]
        self.assertIn('pickup', offered[0])
        self.assertNotIn('pickup', offered[1])
        self.assertIn('pickup', offered[2])
        self.assertIn('pickup', offered[4])

    def test_out_of_zone_drop_needs_a_pose_correction_even_after_repickup(self):
        self.engine.state.side_effect = [{**state(), 'holding': True}, state(), {**state(), 'holding': True}]
        self.engine.execute.side_effect = [{'success': True, 'in_zone': False}, {'success': True}]
        self.claude.decide.side_effect = [Decision('drop', {}, {}), Decision('pickup', {}, {}),
                                        Decision('drop', {}, {})]
        result = self.run_rover(max_decisions=3)
        self.assertEqual(self.engine.execute.call_count, 2)
        self.assertEqual(self.records(result)[2]['result']['outcome'], 'not_dispatched')

    def test_camera_refresh_reuse_and_artifacts(self):
        self.engine.look.side_effect = [jpeg("red"), jpeg("green"), jpeg("blue")]
        self.engine.state.side_effect = [
            state(), state("Blocked", 1), state(), {**state("Carry"), "holding": True}, state("Done"),
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
                    self.assertEqual(set(context), {"command", "state", "remaining_decisions", "recent_actions", "previous_visual_observation"})
                    self.assertEqual(context["command"], command)
                    self.assertEqual(set(context["state"]), {"state", "x", "y", "heading", "holding", "retry_count"})
                    self.assertEqual(names, {"move", "turn", "pickup"})
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

    def test_failed_turn_recovery_uses_refreshed_frame_and_action_history(self):
        self.engine.state.side_effect = [state(), state("Blocked"), state()]
        self.engine.look.side_effect = [jpeg("red"), jpeg("blue"), jpeg("white")]
        self.engine.execute.side_effect = [
            {"success": False}, {"success": True, "collided": False},
        ]
        self.claude.decide.side_effect = [
            Decision("turn", {"degrees": 45}, {}),
            Decision("move", {"distance": 0.2}, {}),
        ]
        result = self.run_rover(max_decisions=2)
        first, recovery = self.claude.decide.call_args_list
        blocks = recovery.args[0][0]["content"]
        context = json.loads(blocks[0]["text"])
        self.assertEqual(context["state"]["state"], "Blocked")
        self.assertEqual(context["recent_actions"], [
            {"tool": "turn", "args": {"degrees": 45}, "result": {"success": False}},
        ])
        self.assertIn("Do not repeat the failed turn", recovery.kwargs["system"])
        self.assertNotEqual(first.args[0][0]["content"][1], blocks[1])
        self.assertEqual(base64.b64decode(blocks[1]["source"]["data"]),
                         (result.run_dir / "0001.png").read_bytes())
        self.assertEqual([call.args for call in self.engine.execute.call_args_list],
                         [("turn", {"degrees": 45}), ("move", {"distance": 0.2})])

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
