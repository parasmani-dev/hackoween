"""Keep camera detours and collision recovery on the same chosen side."""

import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch

from agent.claude import Decision
from agent.routing import CameraRoute
from agent.runner import run_command
from agent.test_runner import jpeg, state


class RecoveryRouteCoordinationTests(unittest.TestCase):
    def test_recovery_side_overrides_opposite_default_camera_preference(self):
        default = CameraRoute()
        default._points[(0, 0)] = (0.60, -0.28)
        self.assertEqual(default.propose("move", {"distance": 0.5}, state()),
                         ("turn", {"degrees": -45}))
        recovering = CameraRoute()
        recovering._points[(0, 0)] = (0.60, -0.28)
        self.assertEqual(recovering.propose("move", {"distance": 0.5}, state(), preferred_side=1),
                         ("turn", {"degrees": 45}))
        self.assertEqual(recovering._sign, 1)

    def test_contact_discards_old_detour_but_keeps_observed_obstacle_geometry(self):
        route = CameraRoute()
        route._points[(0, 0)], route._points[(1, 0)] = (0.60, -0.28), (0.70, -0.28)
        route._edges.add(((0, 0), (1, 0)))
        route._origin, route._travel_heading, route._sign = (0, 0, 0), -45, -1
        points, edges = dict(route._points), set(route._edges)
        route.contact()
        self.assertIsNone(route._origin)
        self.assertIsNone(route._travel_heading)
        self.assertIsNone(route._sign)
        self.assertEqual(dict(route._points), points)
        self.assertEqual(route._edges, edges)
        proposal = route.propose("move", {"distance": 0.5}, state(), preferred_side=1)
        self.assertEqual(proposal, ("turn", {"degrees": 45}))

    @staticmethod
    def routing_mock():
        route = Mock()
        route.propose.return_value = None
        route.observe.return_value = False
        route.guidance.return_value = ""
        route.search_turn.return_value = None
        return route

    def test_runner_forwards_chosen_recovery_side_after_safe_retreat(self):
        engine = Mock()
        engine.look.return_value = jpeg("red")
        engine.state.side_effect = [
            state(), state("Blocked"), {**state("Blocked"), "heading": 45},
            {**state("Go to object"), "heading": 45, "x": -0.21, "y": 0.21},
            {**state("Go to object"), "heading": 45, "x": -0.07, "y": 0.07},
        ]
        engine.execute.side_effect = [
            {"success": False, "collided": True}, {"success": True},
            {"success": True, "collided": False}, {"success": True, "collided": False},
        ]
        policy = Mock()
        policy.decide.side_effect = [
            Decision("move", {"distance": 0.5}, {}), Decision("turn", {"degrees": 45}, {}),
            Decision("move", {"distance": -0.3}, {}), Decision("move", {"distance": 0.2}, {}),
        ]
        route = self.routing_mock()
        with TemporaryDirectory() as temporary, patch("agent.runner.CameraRoute", return_value=route):
            result = run_command("Fetch the yellow cone", engine=engine, claude=policy,
                                 output_root=temporary, local_pickup_checks=True, max_decisions=4)
            rows = [json.loads(line) for line in (result.run_dir / "steps.jsonl").read_text().splitlines()]
        self.assertEqual(result.status, "decision_limit")
        self.assertEqual(engine.execute.call_args_list, [
            call("move", {"distance": 0.5}), call("turn", {"degrees": 45}),
            call("move", {"distance": -0.3}), call("move", {"distance": 0.2}),
        ])
        route.contact.assert_called_once_with()
        self.assertEqual(route.propose.call_args_list[0].kwargs["preferred_side"], None)
        self.assertEqual(route.propose.call_args_list[-1].kwargs["preferred_side"], 1)
        self.assertEqual(route.propose.call_args_list[-1].args[:2], ("move", {"distance": 0.2}))
        self.assertTrue(all(row["result"].get("outcome") != "not_dispatched" for row in rows))

    def test_runner_clears_camera_detour_after_failed_turn_as_well(self):
        engine = Mock()
        engine.look.return_value = jpeg("red")
        engine.state.side_effect = [state(), state("Blocked"), {**state("Go to object"), "x": -0.2}]
        engine.execute.side_effect = [{"success": False}, {"success": True, "collided": False}]
        policy = Mock()
        policy.decide.side_effect = [Decision("turn", {"degrees": 45}, {}),
                                     Decision("move", {"distance": -0.2}, {})]
        route = self.routing_mock()
        with TemporaryDirectory() as temporary, patch("agent.runner.CameraRoute", return_value=route):
            result = run_command("Fetch the yellow cone", engine=engine, claude=policy,
                                 output_root=temporary, local_pickup_checks=True, max_decisions=2)
        self.assertEqual(result.decisions, 2)
        route.contact.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
