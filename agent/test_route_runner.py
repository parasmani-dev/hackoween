"""Offline dispatch and artifact checks for camera-route and escape corrections."""

import json
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

from agent.claude import Decision
from agent.runner import run_command
from agent.test_runner import jpeg, state


class RouteRunnerTests(unittest.TestCase):
    def test_camera_detour_replaces_unsafe_drive_and_preserves_fresh_images(self):
        with TemporaryDirectory() as temporary:
            engine = Mock()
            engine.state.side_effect = [state(), {**state(), "heading": -45},
                                        {**state(), "heading": -45, "x": .14, "y": .14}]
            engine.look.side_effect = [jpeg("gray"), jpeg("silver"), jpeg("white")]
            engine.execute.side_effect = [{"success": True}, {"success": True, "collided": False}]
            policy = Mock()
            policy.decide.side_effect = [Decision("move", {"distance": .5}, {}),
                                         Decision("turn", {"degrees": 45}, {})]
            route = Mock()
            route.guidance.return_value = " Continue the camera-observed side route."
            route.propose.side_effect = [("turn", {"degrees": -45}),
                                         ("move", {"distance": .2})]
            with patch("agent.runner.CameraRoute", return_value=route):
                result = run_command("Pick yellow cone", engine=engine, claude=policy,
                                     output_root=temporary, local_pickup_checks=True,
                                     max_decisions=2)
            self.assertEqual(engine.execute.call_args_list,
                             [call("turn", {"degrees": -45}), call("move", {"distance": .2})])
            self.assertEqual(engine.look.call_count, 3)
            corrections = [json.loads(line) for line in
                           (result.run_dir / "adjustments.jsonl").read_text().splitlines()]
            self.assertEqual([row["proposed_tool"] for row in corrections], ["move", "turn"])
            self.assertTrue(all(row["reason"] == "camera_obstacle_route" for row in corrections))
            rows = [json.loads(line) for line in
                    (result.run_dir / "steps.jsonl").read_text().splitlines()]
            self.assertEqual([row["frame_path"] for row in rows], ["0000.png", "0001.png"])

    def test_recovery_rejections_become_bounded_same_side_scans(self):
        with TemporaryDirectory() as temporary:
            engine = Mock()
            base = state()
            engine.state.side_effect = [base, {**base, "state": "Blocked"},
                {**base, "x": -.3}, {**base, "x": -.3, "heading": -8},
                {**base, "x": -.3, "heading": -33},
                {**base, "x": -.3, "heading": -55}]
            engine.look.return_value = jpeg("white")
            engine.execute.side_effect = [{"success": False, "collided": True},
                {"success": True, "collided": False}, {"success": True},
                {"success": True}, {"success": True}]
            policy = Mock()
            policy.decide.side_effect = [Decision(tool, args, {}) for tool, args in [
                ("move", {"distance": .5}), ("move", {"distance": -.3}),
                ("turn", {"degrees": -8}), ("move", {"distance": .3}),
                ("turn", {"degrees": 20})]]
            route = Mock()
            route.guidance.return_value = ""
            route.propose.return_value = None
            with patch("agent.runner.CameraRoute", return_value=route):
                result = run_command("Pick yellow cone", engine=engine, claude=policy,
                                     output_root=temporary, local_pickup_checks=True,
                                     max_decisions=5)
            self.assertEqual(engine.execute.call_args_list[-2:],
                             [call("turn", {"degrees": -25}), call("turn", {"degrees": -22})])
            rows = [json.loads(line) for line in
                    (result.run_dir / "steps.jsonl").read_text().splitlines()]
            self.assertTrue(all(row["result"].get("outcome") != "not_dispatched" for row in rows))
            corrections = [json.loads(line) for line in
                           (result.run_dir / "adjustments.jsonl").read_text().splitlines()]
            self.assertEqual([row["reason"] for row in corrections],
                             ["recovery_escape_scan", "recovery_escape_scan"])


if __name__ == "__main__":
    unittest.main()
