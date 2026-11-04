"""Motor timeout regressions using the production move loop; no Webots import needed."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class MotorTests(unittest.TestCase):
    def drive(self, positions=None, contact=False):
        source = Path(__file__).parent / 'controllers/motor_demo/motor_demo.py'
        spec = importlib.util.spec_from_file_location('stall_test_motor', source)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'controller': SimpleNamespace(Supervisor=Mock())}):
            spec.loader.exec_module(module)
        drive = module.RoverDrive.__new__(module.RoverDrive)
        drive.dt = 0.032
        drive.node = Mock()
        drive.node.getPosition.return_value = [0, 0, 0]
        if positions is not None:
            drive.node.getPosition.side_effect = positions
        drive.heading = lambda: 0
        drive.contact = lambda: contact
        drive.command = Mock()
        drive.step = Mock()
        return drive

    def test_no_contact_stall_brakes_in_both_directions(self):
        for distance in (0.5, -0.5):
            with self.subTest(distance=distance):
                drive = self.drive()
                self.assertFalse(drive.move(distance))
                self.assertEqual(drive.step.call_count, 63)
                self.assertGreaterEqual(drive.step.call_count * drive.dt, 2)
                drive.command.assert_called_with(0, 0)
                drive.node.setVelocity.assert_called_once_with([0.0] * 6)

    def test_progress_restarts_stall_timer(self):
        # 1.92 seconds stationary, then progress, then another two-second stall.
        drive = self.drive([[0, 0, 0]] * 61 + [[0.01, 0, 0]] * 64)
        self.assertFalse(drive.move(2.0))
        self.assertEqual(drive.step.call_count, 124)

    def test_contact_stops_immediately(self):
        drive = self.drive(contact=True)
        self.assertFalse(drive.move(0.5))
        self.assertEqual(drive.step.call_count, 1)
        drive.node.setVelocity.assert_called_once()

    def test_step_failure_always_brakes(self):
        drive = self.drive()
        drive.step.side_effect = RuntimeError('simulator disconnected')
        with self.assertRaises(RuntimeError):
            drive.move(0.5)
        drive.command.assert_called_with(0, 0)
        drive.node.setVelocity.assert_called_once()


if __name__ == '__main__':
    unittest.main()
