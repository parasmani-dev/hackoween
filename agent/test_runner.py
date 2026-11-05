"""Phase 2 loop checks with mocked clients and real PNG/JSONL artifacts."""

import base64
import json
import os
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from PIL import Image

from agent.claude import ClaudeError, Decision
from agent.engine import ActionOutcomeUncertain
from agent.runner import RunResult, _alternating_turns, _navigation_prompt, run_command
from agent.navigation import NavigationMemory


def jpeg(color):
    with BytesIO() as stream:
        Image.new("RGB", (640, 480), color).save(
            stream, format="JPEG", comment=b"private-metadata"
        )
        return stream.getvalue()


def state(name="Locate", retry_count=0):
    return dict(state=name, x=0, y=0, heading=0,
                holding=name in ("Carry", "Drop"), retry_count=retry_count)


class RunnerTests(unittest.TestCase):
    def controlled_tracker(self, confidence=()):
        """Mock only pixel association; keep runner/action feedback real."""
        tracker = Mock()
        tracker.active = False
        updates = iter(confidence)

        def start(path):
            tracker.active = True
            return True

        def update(path):
            confident = next(updates, True)
            tracker.active = confident
            return confident

        tracker.start.side_effect = start
        tracker.update.side_effect = update
        tracker.reset.side_effect = lambda: setattr(tracker, "active", False)
        return tracker

    def test_tracked_approach_batches_keep_action_and_request_audits_for_both_providers(self):
        for provider, module in (("claude", "claude"), ("openai", "openai_client")):
            for cap in (5, 6):
                with self.subTest(provider=provider, cap=cap):
                    self.engine.reset_mock()
                    self.engine.execute.side_effect = [
                        {"success": False}, {"success": True, "collided": False},
                        {"success": False}, {"success": True, "collided": False},
                        {"success": True}, {"success": True, "in_zone": True},
                    ]
                    self.engine.state.side_effect = [
                        state(), state(), {**state("Go to object"), "x": 0.02},
                        {**state("Go to object"), "x": 0.02}, {**state("Go to object"), "x": 0.04},
                        {**state("Carry"), "x": 0.04}, {**state("Done"), "x": 0.04},
                    ]
                    responses = []
                    note = "target_base=clipped; The requested object extends below the image."
                    for name in ("pickup", "drop"):
                        if provider == "claude":
                            body = {
                                "stop_reason": "tool_use", "content": [
                                    {"type": "text", "text": "OBSERVATION: " + note},
                                    {"type": "tool_use", "name": name, "input": {}},
                                ], "usage": {"input_tokens": 1, "output_tokens": 1},
                            }
                        else:
                            body = {"choices": [{"finish_reason": "tool_calls", "message": {
                                "content": "OBSERVATION: " + note,
                                "tool_calls": [{"type": "function", "function": {
                                    "name": name, "arguments": "{}"}}],
                            }}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
                        responses.append(("application/json", json.dumps(body).encode()))
                    tracker = self.controlled_tracker()
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "offline-test-key", "CLAUDE_MODEL": "offline-claude",
                        "OPENAI_API_KEY": "offline-test-key", "OPENAI_MODEL": "offline-openai",
                    }), patch(f"agent.{module}.request", side_effect=responses) as request, \
                         patch("agent.runner.ApproachTracker", return_value=tracker):
                        result = run_command("Fetch the requested object", engine=self.engine,
                                             provider=provider, output_root=self.temp.name,
                                             local_pickup_checks=True, audit_requests=True,
                                             max_decisions=cap)
                    rows = self.records(result)
                    self.assertEqual(result.decisions, cap)
                    expected_requests = 2 if cap == 6 else 1
                    self.assertEqual(result.model_requests, expected_requests)
                    self.assertEqual(request.call_count, expected_requests)
                    self.assertEqual(result.usage["input_tokens"], expected_requests)
                    self.assertEqual(self.engine.execute.call_count, cap)
                    self.assertEqual(self.engine.look.call_count, 3)
                    tracker.start.assert_called_once_with(result.run_dir / "0000.png")
                    self.assertEqual([call.args[0].name for call in tracker.update.call_args_list],
                                     ["0000.png", "0002.png"])
                    self.assertFalse(tracker.active)
                    sources = [json.loads(line) for line in
                               (result.run_dir / "decision_sources.jsonl").read_text().splitlines()]
                    self.assertEqual([row["source"] for row in sources[:5]],
                                     ["model", "local_approach_probe", "local_pickup_check",
                                      "local_approach_probe", "local_pickup_check"])
                    self.assertTrue(all(row["request_index"] is None for row in sources[1:5]))
                    self.assertEqual([row["tool"] for row in rows[:5]],
                                     ["pickup", "move", "pickup", "move", "pickup"])
                    self.assertEqual(rows[1]["args"], {"distance": 0.02})
                    self.assertEqual(rows[2]["frame_path"], rows[3]["frame_path"])
                    audit = [json.loads(line) for line in
                             (result.run_dir / f"{provider}_requests.jsonl").read_text().splitlines()]
                    self.assertEqual(audit, [call.kwargs["payload"] for call in request.call_args_list])
                    if cap == 6:
                        self.assertEqual(result.status, "engine_done")
                        self.assertEqual(sources[5], {"step": 6, "source": "model", "request_index": 2})
                        if provider == "claude":
                            encoded = audit[1]["messages"][0]["content"][1]["source"]["data"]
                        else:
                            encoded = audit[1]["messages"][1]["content"][1]["image_url"]["url"].split(",", 1)[1]
                        self.assertEqual(base64.b64decode(encoded),
                                         (result.run_dir / rows[5]["frame_path"]).read_bytes())
                    else:
                        self.assertEqual(result.status, "decision_limit")

    def test_tracker_loss_and_contact_hand_control_back_to_model(self):
        for failure in ("confidence", "contact"):
            with self.subTest(failure=failure):
                self.engine.reset_mock()
                self.claude.reset_mock()
                tracker = self.controlled_tracker([failure != "confidence"])
                self.claude.decide.side_effect = [
                    Decision("pickup", {}, {}, observation="target_base=clipped; Target is close."),
                    Decision("turn", {"degrees": 45}, {}),
                ]
                if failure == "confidence":
                    self.engine.execute.side_effect = [{"success": False}, {"success": True}]
                    self.engine.state.side_effect = [state(), state(), {**state(), "heading": 45}]
                    cap = 2
                else:
                    self.engine.execute.side_effect = [
                        {"success": False}, {"success": False, "collided": True}, {"success": True},
                    ]
                    self.engine.state.side_effect = [
                        state(), state(), state("Blocked"), {**state("Blocked"), "heading": 45},
                    ]
                    cap = 3
                with patch("agent.runner.ApproachTracker", return_value=tracker):
                    result = self.run_rover(local_pickup_checks=True, max_decisions=cap)
                self.assertEqual(result.model_requests, 2)
                self.assertEqual(self.claude.decide.call_count, 2)
                self.assertFalse(tracker.active)
                sources = [json.loads(line) for line in
                           (result.run_dir / "decision_sources.jsonl").read_text().splitlines()]
                self.assertEqual(sources[-1]["source"], "model")
                self.assertEqual(self.records(result)[-1]["tool"], "turn")

    def test_unknown_target_base_does_not_acquire_pixel_tracker_or_start_a_batch(self):
        tracker = self.controlled_tracker()
        self.engine.execute.side_effect = [{"success": False}, {"success": True, "collided": False}]
        self.engine.state.side_effect = [state(), state(), {**state(), "x": 0.02}]
        self.claude.decide.side_effect = [
            Decision("pickup", {}, {}, observation="target_base=unknown; The target base is occluded."),
            Decision("move", {"distance": 0.02}, {}),
        ]
        with patch("agent.runner.ApproachTracker", return_value=tracker):
            result = self.run_rover(local_pickup_checks=True, max_decisions=2)
        tracker.start.assert_not_called()
        tracker.update.assert_not_called()
        self.assertEqual(result.model_requests, 2)
        sources = [json.loads(line) for line in
                   (result.run_dir / "decision_sources.jsonl").read_text().splitlines()]
        self.assertEqual([row["source"] for row in sources], ["model", "model"])

    def test_local_approach_batch_limit_and_total_decision_cap_are_independent(self):
        for cap in (20, 26):
            with self.subTest(cap=cap):
                self.engine.reset_mock()
                self.claude.reset_mock()
                pose = state()

                def execute(name, args):
                    if name == "pickup":
                        return {"success": False}
                    if name == "move":
                        pose["x"] += args["distance"]
                        return {"success": True, "collided": False}
                    pose["heading"] += args["degrees"]
                    return {"success": True}

                self.engine.execute.side_effect = execute
                self.engine.state.side_effect = lambda: dict(pose)
                self.claude.decide.side_effect = [
                    Decision("pickup", {}, {}, observation="target_base=clipped; Target is close."),
                    Decision("turn", {"degrees": 45}, {}),
                ]
                tracker = self.controlled_tracker()
                with patch("agent.runner.ApproachTracker", return_value=tracker):
                    result = self.run_rover(local_pickup_checks=True, max_decisions=cap)
                sources = [json.loads(line) for line in
                           (result.run_dir / "decision_sources.jsonl").read_text().splitlines()]
                self.assertEqual(result.decisions, cap)
                self.assertEqual(self.engine.execute.call_count, cap)
                self.assertEqual(len(self.records(result)), cap)
                tracker.start.assert_called_once()
                if cap == 26:
                    self.assertEqual(result.model_requests, 2)
                    self.assertEqual(sum(row["source"] == "local_approach_probe" for row in sources), 12)
                    self.assertEqual(sources[-1]["source"], "model")
                    self.assertFalse(tracker.active)
                else:
                    self.assertEqual(result.model_requests, 1)
                    self.assertEqual(sum(row["source"] == "local_approach_probe" for row in sources), 10)
                    self.assertEqual(sum(row["source"] == "local_pickup_check" for row in sources), 9)

    def test_rejected_turn_temporarily_masks_tools_for_factory_clients_only(self):
        for provider, module in (("claude", "claude"), ("openai", "openai_client")):
            for cap in (5, 6):
                with self.subTest(provider=provider, cap=cap):
                    self.engine.reset_mock()
                    self.engine.execute.side_effect = [
                        {"success": True}, {"success": True}, {"success": True},
                        {"success": True, "collided": False}, {"success": True},
                    ]
                    self.engine.state.side_effect = None
                    self.engine.state.return_value = state()
                    responses = []
                    proposals = [("turn", {"degrees": angle}) for angle in (-15, 10, -10, 15)]
                    proposals += [("move", {"distance": 0.2}), ("pickup", {})]
                    for name, args in proposals:
                        if provider == "claude":
                            body = {
                                "stop_reason": "tool_use",
                                "content": [{"type": "tool_use", "name": name, "input": args}],
                                "usage": {"input_tokens": 1, "output_tokens": 1},
                            }
                        else:
                            body = {
                                "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
                                    {"type": "function", "function": {"name": name,
                                                                      "arguments": json.dumps(args)}}
                                ]}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                            }
                        responses.append(("application/json", json.dumps(body).encode()))
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "offline-test-key", "CLAUDE_MODEL": "offline-claude",
                        "OPENAI_API_KEY": "offline-test-key", "OPENAI_MODEL": "offline-openai",
                    }), patch(f"agent.{module}.request", side_effect=responses) as request:
                        result = run_command("Fetch the requested object", engine=self.engine,
                                             provider=provider, output_root=self.temp.name,
                                             audit_requests=True, max_decisions=cap)
                    payloads = [call.kwargs["payload"] for call in request.call_args_list]

                    def names(payload):
                        return {tool["name"] if provider == "claude" else tool["function"]["name"]
                                for tool in payload["tools"]}

                    all_tools = {"move", "turn", "pickup", "drop"}
                    self.assertTrue(all(names(payload) == all_tools for payload in payloads[:4]))
                    self.assertEqual(names(payloads[4]), {"move"})
                    if cap == 6:
                        self.assertEqual(names(payloads[5]), all_tools)
                    self.assertEqual(result.model_requests, cap)
                    self.assertEqual(request.call_count, cap)
                    self.assertEqual(self.engine.execute.call_count, cap - 1)
                    self.assertEqual([call.args for call in self.engine.execute.call_args_list[:3]],
                                     [("turn", {"degrees": -15}), ("turn", {"degrees": 10}),
                                      ("turn", {"degrees": -10})])
                    self.assertEqual(self.engine.execute.call_args_list[3].args,
                                     ("move", {"distance": 0.2}))
                    self.assertEqual(self.records(result)[3]["result"]["reason"], "navigation_guard")

    def test_injected_scripted_policy_keeps_original_decide_signature_after_rejection(self):
        class ScriptedPolicy:
            def __init__(self):
                self.actions = iter(
                    [("turn", {"degrees": angle}) for angle in (-15, 10, -10, 15)]
                    + [("move", {"distance": 0.2})]
                )

            def decide(self, messages, *, system):
                name, args = next(self.actions)
                return Decision(name, args, {})

        result = run_command("Offline generic turn-loop exercise", engine=self.engine,
                             claude=ScriptedPolicy(), output_root=self.temp.name, max_decisions=5)
        self.assertEqual(result.decisions, 5)
        self.assertEqual(self.engine.execute.call_count, 4)
        self.assertEqual(self.records(result)[3]["result"]["reason"], "navigation_guard")
        self.engine.execute.assert_called_with("move", {"distance": 0.2})

    def test_local_pickup_profile_maps_requests_and_respects_total_cap_for_both_providers(self):
        for provider, module in (("claude", "claude"), ("openai", "openai_client")):
            for cap in (3, 4):
                with self.subTest(provider=provider, cap=cap):
                    self.engine.reset_mock()
                    self.engine.execute.side_effect = [
                        {"success": False}, {"success": True, "collided": False},
                        {"success": True}, {"success": True, "in_zone": True},
                    ]
                    self.engine.state.side_effect = [
                        state(), state(), {**state("Go to object"), "x": 0.02},
                        {**state("Carry"), "x": 0.02}, {**state("Done"), "x": 0.02},
                    ]
                    responses = []
                    for name, args in (("pickup", {}), ("move", {"distance": 0.02}), ("drop", {})):
                        if provider == "claude":
                            body = {
                                "stop_reason": "tool_use",
                                "content": [{"type": "tool_use", "name": name, "input": args}],
                                "usage": {"input_tokens": 1, "output_tokens": 1},
                            }
                        else:
                            body = {
                                "choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
                                    {"type": "function", "function": {"name": name,
                                                                      "arguments": json.dumps(args)}}
                                ]}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                            }
                        responses.append(("application/json", json.dumps(body).encode()))
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "offline-test-key", "CLAUDE_MODEL": "offline-claude",
                        "OPENAI_API_KEY": "offline-test-key", "OPENAI_MODEL": "offline-openai",
                    }), patch(f"agent.{module}.request", side_effect=responses) as request:
                        result = run_command("Fetch the requested object", engine=self.engine,
                                             provider=provider, output_root=self.temp.name,
                                             local_pickup_checks=True, audit_requests=True,
                                             max_decisions=cap)
                    rows = self.records(result)
                    self.assertEqual(result.decisions, cap)
                    self.assertEqual(result.model_requests, cap - 1)
                    self.assertTrue(result.local_pickup_checks)
                    self.assertEqual(request.call_count, cap - 1)
                    self.assertEqual(result.usage["input_tokens"], cap - 1)
                    self.assertEqual(self.engine.execute.call_count, cap)
                    self.assertEqual(self.engine.look.call_count, 2)
                    self.assertEqual(rows[2]["tool"], "pickup")
                    self.assertTrue(rows[2]["result"]["success"])
                    sources = [json.loads(line) for line in
                               (result.run_dir / "decision_sources.jsonl").read_text().splitlines()]
                    self.assertEqual(sources[2],
                                     {"step": 3, "source": "local_pickup_check", "request_index": None})
                    self.assertEqual([row["source"] for row in sources[:2]], ["model", "model"])
                    audit = [json.loads(line) for line in
                             (result.run_dir / f"{provider}_requests.jsonl").read_text().splitlines()]
                    self.assertEqual(audit, [call.kwargs["payload"] for call in request.call_args_list])
                    self.assertEqual(len(audit), cap - 1)
                    if cap == 4:
                        self.assertEqual(result.status, "engine_done")
                        self.assertEqual(sources[3],
                                         {"step": 4, "source": "model", "request_index": 3})
                        self.assertEqual(rows[2]["frame_path"], rows[3]["frame_path"])
                        if provider == "claude":
                            encoded = audit[2]["messages"][0]["content"][1]["source"]["data"]
                        else:
                            encoded = audit[2]["messages"][1]["content"][1]["image_url"]["url"].split(",", 1)[1]
                        self.assertEqual(base64.b64decode(encoded),
                                         (result.run_dir / rows[3]["frame_path"]).read_bytes())
                    else:
                        self.assertEqual(result.status, "decision_limit")
                        self.assertEqual(len(rows), 3)

    def test_local_pickup_profile_maps_provider_failure_without_dispatch(self):
        self.claude.decide.side_effect = ClaudeError("private provider detail")
        result = self.run_rover(local_pickup_checks=True, max_decisions=4)
        self.assertEqual(result.status, "claude_error")
        self.assertEqual(result.model_requests, 1)
        self.assertEqual(result.decisions, 1)
        self.engine.execute.assert_not_called()
        sources = [json.loads(line) for line in
                   (result.run_dir / "decision_sources.jsonl").read_text().splitlines()]
        self.assertEqual(sources, [{"step": 1, "source": "model", "request_index": 1}])
        self.assertEqual(self.records(result)[0]["result"]["reason"], "provider_error")

    def test_cli_enables_local_pickup_profile_with_explicit_model_only_optout(self):
        from agent import __main__ as cli

        def completed(*args, **kwargs):
            return RunResult("engine_done", 0, {}, Path(self.temp.name),
                             local_pickup_checks=kwargs["local_pickup_checks"])

        for extra, enabled in (([], True), (["--model-pickup-checks"], False)):
            with self.subTest(enabled=enabled), \
                 patch("sys.argv", ["agent", "Fetch the requested object", *extra]), \
                 patch("sys.stdout", new_callable=StringIO) as output, \
                 patch.object(cli, "run_command", side_effect=completed) as runner:
                self.assertEqual(cli.main(), 0)
                self.assertEqual(runner.call_args.kwargs["local_pickup_checks"], enabled)
                self.assertEqual(json.loads(output.getvalue())["local_pickup_checks"], enabled)

    def test_repeated_collision_guidance_does_not_mislabel_safe_turn_as_reversal(self):
        history = [
            {"tool": "move", "args": {"distance": 0.4},
             "result": {"success": False, "collided": True}},
            {"tool": "move", "args": {"distance": -0.3},
             "result": {"success": True, "collided": False}},
        ] * 2
        prompt = _navigation_prompt(history)
        self.assertIn("Do not drive back into that corridor", prompt)
        self.assertIn("Do not pick up a different object", prompt)
        self.assertNotIn("Your last three successful turns alternated", prompt)
        self.assertFalse(_alternating_turns(history))

    def test_collision_memory_blocks_same_corridor_but_allows_retreat_and_detour(self):
        self.claude.decide.side_effect = [
            Decision("move", {"distance": 0.4}, {}),
            Decision("move", {"distance": -0.3}, {}),
            Decision("move", {"distance": 0.4}, {}),
            Decision("turn", {"degrees": 45}, {}),
            Decision("move", {"distance": 0.2}, {}),
        ]
        self.engine.execute.side_effect = [
            {"success": False, "collided": True},
            {"success": True, "collided": False},
            {"success": True}, {"success": True, "collided": False},
        ]
        self.engine.state.side_effect = [
            state(), state("Blocked"), state(), {**state(), "heading": 45},
            {**state(), "x": 0.1, "y": -0.1, "heading": 45},
        ]
        result = self.run_rover(max_decisions=5)
        rows = self.records(result)
        self.assertEqual(rows[2]["result"]["reason"], "navigation_guard")
        self.assertEqual(rows[2]["frame_path"], rows[3]["frame_path"])
        self.assertEqual(self.engine.execute.call_count, 4)
        self.assertEqual(rows[4]["result"]["success"], True)
        events = [json.loads(line) for line in (result.run_dir / "navigation.jsonl").read_text().splitlines()]
        self.assertEqual(events[0]["reason"], "previously_blocked_approach")

    def test_failed_pickup_needs_new_viewpoint_for_any_command(self):
        for command in ("Fetch the purple cube", "Fetch the yellow cone", "Fetch the striped object"):
            with self.subTest(command=command):
                self.engine.reset_mock()
                self.engine.execute.side_effect = [
                    {"success": False}, {"success": True, "collided": False}, {"success": True},
                ]
                self.claude.decide.side_effect = [
                    Decision("pickup", {}, {}), Decision("pickup", {}, {}),
                    Decision("move", {"distance": 0.02}, {}), Decision("pickup", {}, {}),
                ]
                self.engine.state.side_effect = [
                    state(), state(), {**state(), "x": 0.02},
                    {**state("Carry"), "x": 0.02},
                ]
                result = run_command(command, engine=self.engine, claude=self.claude,
                                     output_root=self.temp.name, max_decisions=4)
                rows = self.records(result)
                self.assertEqual(rows[1]["result"]["reason"], "navigation_guard")
                self.assertTrue(rows[-1]["result"]["success"])
                self.assertEqual(self.engine.execute.call_count, 3)

    def test_close_range_probe_stops_before_another_forward_move_for_both_providers(self):
        for provider in ("claude", "openai"):
            with self.subTest(provider=provider):
                self.engine.reset_mock()
                self.claude.reset_mock()
                self.engine.execute.side_effect = [
                    {"success": False}, {"success": True, "collided": False}, {"success": True},
                ]
                self.engine.state.side_effect = [
                    state(), state(), {**state(), "x": 0.02},
                    {**state("Carry"), "x": 0.02},
                ]
                self.claude.decide.side_effect = [
                    Decision("pickup", {}, {}),
                    Decision("move", {"distance": 0.2}, {}),
                    Decision("move", {"distance": 0.02}, {}),
                    Decision("move", {"distance": 0.02}, {}),
                    Decision("pickup", {}, {}),
                ]
                result = run_command("Fetch the requested object", engine=self.engine,
                                     claude=self.claude, provider=provider,
                                     output_root=self.temp.name, max_decisions=5)
                rows = self.records(result)
                self.assertEqual(self.engine.execute.call_args_list[1].args,
                                 ("move", {"distance": 0.02}))
                self.assertEqual(self.engine.execute.call_count, 3)
                self.assertEqual(rows[1]["result"],
                                 {"outcome": "not_dispatched", "reason": "navigation_guard"})
                self.assertEqual(rows[3]["result"],
                                 {"outcome": "not_dispatched", "reason": "navigation_guard"})
                self.assertEqual(rows[0]["frame_path"], rows[2]["frame_path"])
                self.assertEqual(rows[3]["frame_path"], rows[4]["frame_path"])
                self.assertNotEqual(rows[2]["frame_path"], rows[4]["frame_path"])
                events = [json.loads(line) for line in
                          (result.run_dir / "navigation.jsonl").read_text().splitlines()]
                self.assertEqual([event["reason"] for event in events],
                                 ["near_target_step_too_large", "pickup_check_required"])
                self.assertTrue(rows[-1]["result"]["success"])

                # A new command has no stale near-target probe lock.
                self.engine.reset_mock()
                self.engine.execute.side_effect = None
                self.engine.execute.return_value = {"success": True, "collided": False}
                self.engine.state.side_effect = None
                self.engine.state.return_value = state()
                self.claude.decide.side_effect = None
                self.claude.decide.return_value = Decision("move", {"distance": 0.2}, {})
                fresh = run_command("Fetch a different object", engine=self.engine,
                                    claude=self.claude, provider=provider,
                                    output_root=self.temp.name, max_decisions=1)
                self.assertTrue(self.records(fresh)[0]["result"]["success"])
                self.engine.execute.assert_called_once_with("move", {"distance": 0.2})

    def test_pending_probe_requires_pickup_before_alignment_turn_for_both_providers(self):
        for provider in ("claude", "openai"):
            with self.subTest(provider=provider):
                self.engine.reset_mock()
                self.claude.reset_mock()
                self.engine.execute.side_effect = [
                    {"success": False}, {"success": True, "collided": False}, {"success": True},
                ]
                self.engine.state.side_effect = [
                    state(), state(), {**state("Go to object"), "x": 0.02},
                    {**state("Carry"), "x": 0.02},
                ]
                self.claude.decide.side_effect = [
                    Decision("pickup", {}, {"input_tokens": 1}),
                    Decision("move", {"distance": 0.02}, {"input_tokens": 1}),
                    Decision("turn", {"degrees": 6}, {"input_tokens": 1}),
                    Decision("pickup", {}, {"input_tokens": 1}),
                ]
                result = run_command("Fetch the requested object", engine=self.engine,
                                     claude=self.claude, provider=provider,
                                     output_root=self.temp.name, max_decisions=4)
                rows = self.records(result)
                self.assertEqual(self.engine.execute.call_count, 3)
                self.assertEqual([call.args for call in self.engine.execute.call_args_list],
                                 [("pickup", {}), ("move", {"distance": 0.02}), ("pickup", {})])
                self.assertEqual(rows[2]["tool"], "turn")
                self.assertEqual(rows[2]["result"],
                                 {"outcome": "not_dispatched", "reason": "navigation_guard"})
                self.assertEqual(rows[2]["frame_path"], rows[3]["frame_path"])
                self.assertEqual(self.engine.look.call_count, 2)
                self.assertEqual(result.usage["input_tokens"], 4)
                events = [json.loads(line) for line in
                          (result.run_dir / "navigation.jsonl").read_text().splitlines()]
                self.assertEqual(events[0]["reason"], "pickup_check_required")
                self.assertTrue(rows[-1]["result"]["success"])

    def test_noop_turn_new_frame_does_not_unlock_failed_pickup(self):
        self.engine.execute.side_effect = [
            {"success": False}, {"success": True},
            {"success": True, "collided": False}, {"success": True},
        ]
        self.engine.state.side_effect = [
            state(), state(), state(), {**state(), "x": 0.02},
            {**state("Carry"), "x": 0.02},
        ]
        self.claude.decide.side_effect = [
            Decision("pickup", {}, {}), Decision("turn", {"degrees": 3}, {}),
            Decision("pickup", {}, {}), Decision("move", {"distance": 0.02}, {}),
            Decision("pickup", {}, {}),
        ]
        result = self.run_rover(max_decisions=5)
        rows = self.records(result)
        self.assertNotEqual(rows[0]["frame_path"], rows[2]["frame_path"])
        self.assertEqual(rows[2]["result"]["reason"], "navigation_guard")
        events = [json.loads(line) for line in
                  (result.run_dir / "navigation.jsonl").read_text().splitlines()]
        self.assertEqual(events[0]["reason"], "pickup_requires_new_viewpoint")
        self.assertEqual(self.engine.execute.call_count, 4)
        self.assertTrue(rows[-1]["result"]["success"])

    def test_holding_preconditions_and_useless_turn_rejected_before_dispatch(self):
        self.engine.execute.side_effect = [
            {"success": True}, {"success": True, "in_zone": True},
        ]
        self.engine.state.side_effect = [state(), state("Carry"), state("Done")]
        self.claude.decide.side_effect = [
            Decision("drop", {}, {}), Decision("turn", {"degrees": 2}, {}),
            Decision("pickup", {}, {}), Decision("pickup", {}, {}), Decision("drop", {}, {}),
        ]
        result = self.run_rover(max_decisions=5)
        self.assertEqual(result.status, "engine_done")
        self.assertEqual(self.engine.execute.call_count, 2)
        self.assertEqual(self.engine.look.call_count, 1)
        events = [json.loads(line) for line in
                  (result.run_dir / "navigation.jsonl").read_text().splitlines()]
        self.assertEqual([event["reason"] for event in events],
                         ["nothing_to_drop", "turn_requires_useful_angle", "already_holding"])
        rows = self.records(result)
        self.assertEqual([row["tool"] for row in rows], ["drop", "turn", "pickup", "pickup", "drop"])
        for index in (0, 1, 3):
            self.assertEqual(rows[index]["result"]["outcome"], "not_dispatched")

    def test_probe_memory_is_local_clears_on_pickup_and_yields_to_collision_recovery(self):
        memory = NavigationMemory()
        pose = {**state(), "heading": 179}
        memory.observe("pickup", {}, {"success": False}, pose, pose, "0000.png")
        near = {**pose, "x": 0.02, "heading": -179}
        self.assertEqual(memory.rejection("move", {"distance": 0.2}, near, "0001.png"),
                         "near_target_step_too_large")
        self.assertIsNone(memory.rejection("move", {"distance": 0.02}, near, "0001.png"))
        self.assertIsNone(memory.rejection("move", {"distance": -0.2}, near, "0001.png"))
        self.assertIsNone(memory.rejection("move", {"distance": 0.2},
                                          {**near, "x": 0.6}, "0001.png"))
        self.assertIsNone(memory.rejection("move", {"distance": 0.2},
                                          {**near, "heading": 90}, "0001.png"))
        memory.observe("move", {"distance": 0.02}, {"success": True, "collided": False},
                       pose, near, "0000.png")
        self.assertEqual(memory.rejection("move", {"distance": 0.02}, near, "0001.png"),
                         "pickup_check_required")
        memory.observe("pickup", {}, {"success": False}, near, near, "0001.png")
        self.assertIsNone(memory.rejection("move", {"distance": 0.02}, near, "0001.png"))
        memory.observe("pickup", {}, {"success": True}, near, near, "0001.png")
        self.assertIsNone(memory.rejection("move", {"distance": 0.2}, near, "0001.png"))

        memory.observe("pickup", {}, {"success": False}, pose, pose, "0002.png")
        memory.observe("move", {"distance": 0.02}, {"success": False, "collided": True},
                       pose, pose, "0002.png")
        # A side route during collision recovery must not inherit probe-size limits.
        side = {**pose, "heading": 89}
        self.assertIsNone(memory.rejection("move", {"distance": 0.2}, side, "0003.png"))
        # The robot can also advance along a laterally cleared parallel corridor;
        # this is still near the failed pickup pose and has the original heading.
        parallel = {**pose, "y": 0.35}
        self.assertIsNone(memory.rejection("move", {"distance": 0.2}, parallel, "0003.png"))

    def test_near_target_forward_probe_limit_is_inclusive_at_two_centimetres(self):
        memory = NavigationMemory()
        pose = state()
        memory.observe("pickup", {}, {"success": False}, pose, pose, "0000.png")
        self.assertIsNone(memory.rejection("move", {"distance": 0.02}, pose, "0000.png"))
        self.assertEqual(memory.rejection("move", {"distance": 0.021}, pose, "0000.png"),
                         "near_target_step_too_large")
        # The probe cap does not prevent a larger retreat from the target.
        self.assertIsNone(memory.rejection("move", {"distance": -0.021}, pose, "0000.png"))

    def test_visible_target_base_rejects_pickup_and_restores_normal_approach_steps(self):
        memory = NavigationMemory()
        pose = state()
        memory.observe("pickup", {}, {"success": False}, pose, pose, "0000.png")
        self.assertEqual(memory.rejection("move", {"distance": 0.2}, pose, "0001.png"),
                         "near_target_step_too_large")
        memory.set_observation("target_base=visible; The object's floor contact is visible.")
        self.assertFalse(memory.approaching(pose))
        self.assertEqual(memory.rejection("pickup", {}, pose, "0001.png"),
                         "pickup_base_still_visible")
        self.assertIsNone(memory.rejection("move", {"distance": 0.2}, pose, "0001.png"))

        for note in ("target_base=clipped; The target extends below the image.",
                     "target_base=unknown; The target base is occluded.", None):
            with self.subTest(observation=note):
                memory.set_observation(note)
                self.assertTrue(memory.approaching(pose))
                self.assertEqual(memory.rejection("move", {"distance": 0.2}, pose, "0001.png"),
                                 "near_target_step_too_large")
                self.assertIsNone(memory.rejection("move", {"distance": 0.02}, pose, "0001.png"))
        # Camera facts are local to this command, not inherited by a new one.
        self.assertIsNone(NavigationMemory().rejection("pickup", {}, pose, "0000.png"))

    def test_camera_base_gate_uses_current_model_note_for_both_providers(self):
        for provider in ("claude", "openai"):
            with self.subTest(provider=provider):
                self.engine.reset_mock()
                self.claude.reset_mock()
                self.engine.execute.side_effect = [
                    {"success": False}, {"success": True, "collided": False}, {"success": True},
                ]
                self.engine.state.side_effect = [
                    state(), state(), {**state("Go to object"), "x": 0.2},
                    {**state("Carry"), "x": 0.2},
                ]
                self.claude.decide.side_effect = [
                    Decision("pickup", {}, {}, observation="target_base=unknown; The base is occluded."),
                    Decision("pickup", {}, {}, observation="target_base=visible; Floor contact is visible."),
                    Decision("move", {"distance": 0.2}, {},
                             observation="target_base=visible; Floor contact is visible."),
                    Decision("pickup", {}, {}, observation="target_base=clipped; The base is below the image."),
                ]
                result = run_command("Fetch the requested object", engine=self.engine,
                                     claude=self.claude, provider=provider,
                                     output_root=self.temp.name, max_decisions=4)
                rows = self.records(result)
                self.assertEqual(self.engine.execute.call_count, 3)
                self.assertEqual([call.args for call in self.engine.execute.call_args_list],
                                 [("pickup", {}), ("move", {"distance": 0.2}), ("pickup", {})])
                self.assertEqual(rows[1]["result"]["outcome"], "not_dispatched")
                self.assertTrue(rows[-1]["result"]["success"])
                self.assertEqual(self.engine.look.call_count, 2)
                events = [json.loads(line) for line in
                          (result.run_dir / "navigation.jsonl").read_text().splitlines()]
                self.assertEqual([event["reason"] for event in events], ["pickup_base_still_visible"])

    def test_motion_guard_handles_wraparound_reverse_and_fresh_commands(self):
        memory = NavigationMemory()
        pose = {**state(), "heading": 179}
        memory.observe("move", {"distance": 0.4}, {"success": False, "collided": True},
                       pose, pose, "0000.png")
        wrapped = {**pose, "heading": -179}
        self.assertEqual(memory.rejection("move", {"distance": 0.2}, wrapped, "0001.png"),
                         "previously_blocked_approach")
        self.assertIsNone(memory.rejection("move", {"distance": -0.2}, wrapped, "0001.png"))
        self.assertIsNone(memory.rejection("move", {"distance": 0.2},
                                          {**wrapped, "x": 2}, "0001.png"))
        self.assertEqual(memory.rejection("move", {"distance": 0}, pose, "0001.png"),
                         "move_requires_progress")
        self.assertIsNone(NavigationMemory().rejection("move", {"distance": 0.2}, pose, "0000.png"))

    def test_search_coverage_survives_short_history_and_resets_for_new_area(self):
        memory = NavigationMemory()
        for heading in range(0, 360, 45):
            memory.record_view({**state(), "x": 1e-5 * (-1) ** (heading // 45),
                                "y": -1e-5, "heading": heading})
        self.assertIn("already been viewed", memory.guidance())
        memory.record_view({**state(), "x": 1.5})
        self.assertNotIn("already been viewed", memory.guidance())
        memory.record_view(state())
        self.assertIn("already been viewed", memory.guidance())
        self.assertEqual(NavigationMemory().guidance(), "")

    def test_detour_requires_real_translation_before_turning_back(self):
        memory = NavigationMemory()
        pose = state()
        memory.observe("move", {"distance": 0.4}, {"success": False, "collided": True},
                       pose, pose, "0000.png")
        turned = {**pose, "heading": 45}
        memory.observe("turn", {"degrees": 45}, {"success": True}, pose, turned, "0001.png")
        self.assertEqual(memory.rejection("turn", {"degrees": -30}, turned, "0002.png"),
                         "recovery_requires_translation")
        self.assertIsNone(memory.rejection("turn", {"degrees": 30}, turned, "0002.png"))
        self.assertIn("have not translated", memory.guidance())
        drift = {**turned, "x": 0.01}
        memory.observe("move", {"distance": 0.2}, {"success": True, "collided": False},
                       turned, drift, "0002.png")
        self.assertIsNotNone(memory.rejection("turn", {"degrees": -30}, drift, "0003.png"))
        moved = {**turned, "x": 0.1, "y": -0.35}
        memory.observe("move", {"distance": 0.2}, {"success": True, "collided": False},
                       drift, moved, "0003.png")
        self.assertIsNone(memory.rejection("turn", {"degrees": -30}, moved, "0004.png"))
        self.assertIsNone(memory.rejection("move", {"distance": 0.2},
                                          {**moved, "heading": 0}, "0004.png"))

    def test_proven_detour_retires_local_bans_without_erasing_distant_collision_evidence(self):
        memory = NavigationMemory()
        collided = {"success": False, "collided": True}
        for x in (3.0, 0.4, 0.0):
            pose = {**state(), "x": x}
            memory.observe("move", {"distance": 0.4}, collided, pose, pose, "0000.png")
        original_evidence = [(row["x"], row["y"], row["direction"])
                             for row in memory.collisions]
        new_approach = {**state(), "x": -0.2, "y": 0.05}
        self.assertEqual(memory.rejection("move", {"distance": 0.04}, new_approach, "0001.png"),
                         "previously_blocked_approach")

        # A side route has actually moved the chassis beyond the footprint's
        # lateral clearance, rather than just rotating its camera in place.
        origin = state()
        turned = {**origin, "heading": 90}
        memory.observe("turn", {"degrees": 90}, {"success": True}, origin, turned, "0001.png")
        translated = {**turned, "y": -0.35}
        memory.observe("move", {"distance": 0.35}, {"success": True, "collided": False},
                       turned, translated, "0002.png")

        # This fresh approach is still inside the old 0.8 m neighbourhood and
        # original direction; only the completed detour makes it eligible again.
        self.assertIsNone(memory.rejection("move", {"distance": 0.04}, new_approach, "0003.png"))
        self.assertEqual([(row["x"], row["y"], row["direction"])
                          for row in memory.collisions], original_evidence)
        distant_approach = {**state(), "x": 2.9}
        self.assertEqual(memory.rejection("move", {"distance": 0.04}, distant_approach, "0003.png"),
                         "previously_blocked_approach")

        # Retiring old local evidence does not grant a permanent exemption if
        # the robot makes contact again on the newly chosen approach.
        memory.observe("move", {"distance": 0.04}, collided,
                       new_approach, new_approach, "0003.png")
        self.assertEqual(memory.rejection("move", {"distance": 0.04}, new_approach, "0004.png"),
                         "previously_blocked_approach")

    def test_detour_preserves_an_independent_nearby_wall_and_handles_heading_wraparound(self):
        memory = NavigationMemory()
        collided = {"success": False, "collided": True}
        orthogonal = {**state(), "x": 0.4, "y": 0.6, "heading": 90}
        co_oriented = {**state(), "x": -0.3, "y": 0.1, "heading": -179}
        latest = {**state(), "heading": 179}
        for pose in (orthogonal, co_oriented, latest):
            memory.observe("move", {"distance": 0.4}, collided, pose, pose, "0000.png")

        turned = {**latest, "heading": -91}
        memory.observe("turn", {"degrees": 90}, {"success": True}, latest, turned, "0001.png")
        translated = {**turned, "x": 0.006, "y": 0.35}
        memory.observe("move", {"distance": 0.35}, {"success": True, "collided": False},
                       turned, translated, "0002.png")

        self.assertEqual(len(memory.collisions), 3)
        # The two wrapped headings differ by only 2 degrees, so both records of
        # the completed detour's original approach should now permit re-entry.
        self.assertIsNone(memory.rejection("move", {"distance": 0.04},
                                          {**latest, "x": 0.2}, "0003.png"))
        self.assertIsNone(memory.rejection("move", {"distance": 0.04},
                                          {**co_oriented, "x": -0.2}, "0003.png"))
        # A second nearby wall was approached in a different direction. Moving
        # around the first wall does not establish clearance through this one.
        self.assertEqual(memory.rejection("move", {"distance": 0.04},
                                           {**orthogonal, "y": 0.7}, "0003.png"),
                         "previously_blocked_approach")

    def test_long_motion_checks_the_swept_segment_before_crossing_known_contact(self):
        memory = NavigationMemory()
        pose = state()
        memory.observe("move", {"distance": 0.4}, {"success": False, "collided": True},
                       pose, pose, "0000.png")
        outside_neighbourhood = {**pose, "x": -1.2}
        self.assertEqual(memory.rejection("move", {"distance": 2.0},
                                           outside_neighbourhood, "0001.png"),
                         "previously_blocked_approach")
        self.assertEqual(memory.rejection("move", {"distance": 2.0},
                                           {**outside_neighbourhood, "y": 0.29}, "0001.png"),
                         "previously_blocked_approach")
        # The start lies outside the old ray's lateral band, but this diagonal
        # converges to within about 0.035 m of the remembered contact point.
        converging = {**outside_neighbourhood, "y": 0.4, "heading": 20}
        self.assertEqual(memory.rejection("move", {"distance": 2.0}, converging, "0001.png"),
                         "previously_blocked_approach")
        # This short step ends before entering the remembered contact area.
        self.assertIsNone(memory.rejection("move", {"distance": 0.2},
                                          outside_neighbourhood, "0001.png"))
        self.assertIsNone(memory.rejection("move", {"distance": -0.2},
                                          outside_neighbourhood, "0001.png"))
        # A parallel corridor outside the footprint clearance is not banned.
        self.assertIsNone(memory.rejection("move", {"distance": 2.0},
                                          {**outside_neighbourhood, "y": 0.35}, "0001.png"))
        # Reverse commands can cross the same area in the blocked travel
        # direction even though the rover itself faces the opposite way.
        reversed_pose = {**outside_neighbourhood, "heading": 180}
        self.assertEqual(memory.rejection("move", {"distance": -2.0},
                                           reversed_pose, "0001.png"),
                         "previously_blocked_approach")

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
