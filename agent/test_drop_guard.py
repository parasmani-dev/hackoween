"""Keep premature release proposals away from an observed floor-zone edge."""

import json
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

from agent.claude import Decision
from agent.runner import run_command
from agent.test_runner import jpeg, state


class DropGuardTests(unittest.TestCase):
    def test_early_drop_keeps_object_and_records_correction_before_confirmed_release(self):
        with TemporaryDirectory() as temporary:
            engine = Mock()
            engine.state.side_effect = [state("Carry"), {**state("Carry"), "x": .1},
                                        {**state("Done"), "x": .1}]
            engine.look.side_effect = [jpeg("gray"), jpeg("silver")]
            engine.execute.side_effect = [{"success": True, "collided": False},
                                          {"success": True, "in_zone": True}]
            policy = Mock()
            policy.decide.side_effect = [Decision("drop", {}, {}), Decision("drop", {}, {})]
            zone = Mock(known=True)
            zone.guidance.return_value = " Approach the camera-observed floor interior."
            zone.ready.side_effect = [False, True]
            zone.correction.return_value = ("move", {"distance": .1})
            with patch("agent.runner.GreenDropZone", return_value=zone):
                result = run_command("Pick red square and drop in green zone", engine=engine,
                                     claude=policy, output_root=temporary,
                                     local_pickup_checks=True, max_decisions=2)
            self.assertEqual(result.status, "engine_done")
            self.assertEqual(engine.execute.call_args_list,
                             [call("move", {"distance": .1}), call("drop", {})])
            self.assertEqual(engine.look.call_count, 2)
            rows = [json.loads(line) for line in
                    (result.run_dir / "steps.jsonl").read_text().splitlines()]
            self.assertEqual([row["frame_path"] for row in rows], ["0000.png", "0001.png"])
            adjustment = json.loads((result.run_dir / "adjustments.jsonl").read_text())
            self.assertEqual(adjustment["proposed_tool"], "drop")
            self.assertEqual(adjustment["applied_tool"], "move")
            self.assertEqual(adjustment["step"], 1)
            self.assertEqual(result.model_requests, 2)

    def test_unavailable_safe_correction_rejects_drop_without_releasing(self):
        with TemporaryDirectory() as temporary:
            engine = Mock()
            engine.state.return_value = state("Carry")
            engine.look.return_value = jpeg("gray")
            policy = Mock()
            policy.decide.return_value = Decision("drop", {}, {})
            zone = Mock(known=True)
            zone.guidance.return_value = ""
            zone.ready.return_value = False
            zone.correction.return_value = None
            with patch("agent.runner.GreenDropZone", return_value=zone):
                result = run_command("Drop in green zone", engine=engine, claude=policy,
                                     output_root=temporary, local_pickup_checks=True,
                                     max_decisions=1)
            engine.execute.assert_not_called()
            event = json.loads((result.run_dir / "navigation.jsonl").read_text())
            self.assertEqual(event["reason"], "drop_outside_observed_zone")


if __name__ == "__main__":
    unittest.main()
