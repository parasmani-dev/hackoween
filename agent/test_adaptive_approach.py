"""Offline integration of calibrated local approaches and pickup feedback."""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

from agent.claude import Decision
from agent.runner import run_command
from agent.test_runner import jpeg, state


class _FloorApproach:
    """Keep a visible-floor anchor until reset; supply bounded test distances."""

    def __init__(self, distances):
        self.anchored = False
        self.distances = iter(distances)
        self.observations = []
        self.distance_calls = []

    def observe(self, path, pose, base_status):
        self.observations.append((Path(path).name, dict(pose), base_status))
        if base_status == "visible":
            self.anchored = True

    def distance(self, path, pose):
        self.distance_calls.append((Path(path).name, dict(pose)))
        return next(self.distances) if self.anchored else None

    def reset(self):
        self.anchored = False


class _Policy:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = 0

    def decide(self, messages, *, system):
        self.calls += 1
        return next(self.decisions)


def _tracker(acquire=True):
    tracker = Mock()
    tracker.active = False

    def start(path):
        tracker.active = acquire
        return acquire

    tracker.start.side_effect = start
    tracker.update.return_value = True
    tracker.reset.side_effect = lambda: setattr(tracker, "active", False)
    return tracker


def _model_decisions():
    return [
        Decision("move", {"distance": 0.1}, {},
                 observation="target_base=visible; Requested target floor contact is visible."),
        Decision("pickup", {}, {},
                 observation="target_base=clipped; Requested target continues below the image."),
    ]


def _response(provider, decision):
    note = "OBSERVATION: " + decision.observation if decision.observation else ""
    if provider == "claude":
        body = {
            "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": note},
                {"type": "tool_use", "name": decision.name, "input": decision.args},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    else:
        body = {
            "choices": [{"finish_reason": "tool_calls", "message": {
                "content": note,
                "tool_calls": [{"type": "function", "function": {
                    "name": decision.name, "arguments": json.dumps(decision.args)}}],
            }}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    return "application/json", json.dumps(body).encode()


def _rows(result, name="steps.jsonl"):
    return [json.loads(line) for line in (result.run_dir / name).read_text().splitlines()]


class AdaptiveApproachTests(unittest.TestCase):
    def test_both_providers_keep_floor_anchor_and_check_after_each_adaptive_probe(self):
        for provider, module in (("claude", "claude"), ("openai", "openai_client")):
            with self.subTest(provider=provider), TemporaryDirectory() as temporary:
                engine = Mock()
                engine.state.side_effect = [
                    state(), {**state("Go to object"), "x": 0.1},
                    {**state("Go to object"), "x": 0.1},
                    {**state("Go to object"), "x": 0.2},
                    {**state("Go to object"), "x": 0.2},
                    {**state("Go to object"), "x": 0.22},
                    {**state("Carry"), "x": 0.22},
                ]
                engine.execute.side_effect = [
                    {"success": True, "collided": False}, {"success": False},
                    {"success": True, "collided": False}, {"success": False},
                    {"success": True, "collided": False}, {"success": True},
                ]
                engine.look.side_effect = [jpeg(color) for color in ("red", "blue", "green", "yellow")]
                floor = _FloorApproach([0.1, 0.02])
                tracker = _tracker()
                with patch.dict(os.environ, {
                    "ANTHROPIC_API_KEY": "offline-test-key", "CLAUDE_MODEL": "offline-claude",
                    "OPENAI_API_KEY": "offline-test-key", "OPENAI_MODEL": "offline-openai",
                }), patch(f"agent.{module}.request", side_effect=[
                    _response(provider, decision) for decision in _model_decisions()
                ]) as request, patch("agent.runner.ApproachTracker", return_value=tracker), \
                        patch("agent.runner.FloorApproach", return_value=floor, create=True):
                    result = run_command("Fetch the requested object", engine=engine,
                                         provider=provider, output_root=temporary,
                                         local_pickup_checks=True, max_decisions=6,
                                         audit_requests=True)

                self.assertEqual((result.decisions, result.model_requests), (6, 2))
                self.assertEqual(request.call_count, 2)
                self.assertEqual(engine.execute.call_args_list, [
                    call("move", {"distance": 0.1}), call("pickup", {}),
                    call("move", {"distance": 0.1}), call("pickup", {}),
                    call("move", {"distance": 0.02}), call("pickup", {}),
                ])
                rows = _rows(result)
                self.assertTrue(rows[-1]["result"]["success"])
                self.assertEqual([row["source"] for row in _rows(result, "decision_sources.jsonl")],
                                 ["model", "model", "local_approach_probe", "local_pickup_check",
                                  "local_approach_probe", "local_pickup_check"])
                self.assertEqual(floor.observations[0], ("0000.png", state(), "visible"))
                self.assertEqual([pose["x"] for _, pose in floor.distance_calls], [0.1, 0.2])
                self.assertEqual(rows[1]["frame_path"], rows[2]["frame_path"])
                self.assertEqual(rows[3]["frame_path"], rows[4]["frame_path"])
                self.assertEqual(engine.look.call_count, 4)

    def test_model_large_move_stays_rejected_after_failed_pickup(self):
        with TemporaryDirectory() as temporary:
            engine = Mock()
            engine.state.side_effect = [state(), {**state("Go to object"), "x": 0.1},
                                        {**state("Go to object"), "x": 0.1}]
            engine.execute.side_effect = [{"success": True, "collided": False}, {"success": False}]
            engine.look.return_value = jpeg("red")
            policy = _Policy(_model_decisions() + [Decision("move", {"distance": 0.1}, {})])
            with patch("agent.runner.ApproachTracker", return_value=_tracker(acquire=False)), \
                    patch("agent.runner.FloorApproach", return_value=_FloorApproach([0.1]), create=True):
                result = run_command("Fetch the requested object", engine=engine, claude=policy,
                                     output_root=temporary, local_pickup_checks=True, max_decisions=3)
            self.assertEqual(policy.calls, 3)
            self.assertEqual(engine.execute.call_args_list,
                             [call("move", {"distance": 0.1}), call("pickup", {})])
            self.assertEqual(_rows(result)[2]["result"],
                             {"outcome": "not_dispatched", "reason": "navigation_guard"})
            self.assertEqual(_rows(result, "navigation.jsonl")[0]["reason"],
                             "near_target_step_too_large")

    def test_zero_safe_distance_returns_to_model_without_local_motion(self):
        with TemporaryDirectory() as temporary:
            engine = Mock()
            engine.state.side_effect = [state(), {**state("Go to object"), "x": 0.1},
                                        {**state("Go to object"), "x": 0.1},
                                        {**state("Go to object"), "x": 0.1, "heading": 30}]
            engine.execute.side_effect = [{"success": True, "collided": False},
                                          {"success": False}, {"success": True}]
            engine.look.return_value = jpeg("red")
            policy = _Policy(_model_decisions() + [Decision("turn", {"degrees": 30}, {})])
            floor = _FloorApproach([0.0])
            with patch("agent.runner.ApproachTracker", return_value=_tracker()), \
                    patch("agent.runner.FloorApproach", return_value=floor, create=True):
                result = run_command("Fetch the requested object", engine=engine, claude=policy,
                                     output_root=temporary, local_pickup_checks=True, max_decisions=3)
            self.assertEqual(policy.calls, 3)
            self.assertEqual(engine.execute.call_args_list, [call("move", {"distance": 0.1}),
                                                           call("pickup", {}),
                                                           call("turn", {"degrees": 30})])
            self.assertEqual(len(floor.distance_calls), 1)
            self.assertEqual([row["source"] for row in _rows(result, "decision_sources.jsonl")],
                             ["model", "model", "model"])


if __name__ == "__main__":
    unittest.main()
