import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from unittest.mock import Mock

from engine.run_control import RunConflict, RunControl


class RunControlTests(unittest.TestCase):
    def test_pickup_and_drop_action_phases(self):
        adapter = Mock()
        control = RunControl(adapter)

        def pickup():
            self.assertEqual(control.phase, "Pick up")
            return {"success": next(pickup_results)}

        def drop():
            self.assertEqual(control.phase, "Drop")
            return {"success": True, "in_zone": False}

        pickup_results = iter((False, True))
        adapter.pickup.side_effect = pickup
        adapter.drop.side_effect = drop
        control.execute("start", (None,), ())
        control.execute("pickup", (), ("success",))
        self.assertEqual((control.phase, control.holding), ("Locate", False))
        control.execute("pickup", (), ("success",))
        self.assertEqual((control.phase, control.holding), ("Carry", True))
        control.execute("drop", (), ("success", "in_zone"))
        self.assertEqual((control.phase, control.holding), ("Drop", False))

    def test_action_exception_restores_phase_and_requires_reset(self):
        adapter = Mock()
        adapter.pickup.side_effect = TimeoutError
        control = RunControl(adapter)
        control.execute("start", (None,), ())
        with self.assertRaises(TimeoutError):
            control.execute("pickup", (), ("success",))
        self.assertEqual(control.phase, "Locate")
        with self.assertRaisesRegex(RunConflict, "^reset_required$"):
            control.execute("pickup", (), ("success",))
        self.assertEqual(
            control.execute("finish", (), ()), {"state": "Done", "outcome": "fail_not_reached"}
        )
        control.execute("reset", (), ())
        self.assertEqual(control.phase, "Idle")
        self.assertFalse(control.needs_reset)

    def test_assessed_start_requires_valid_key_and_evaluator(self):
        adapter = Mock(spec=["reset"])
        control = RunControl(adapter)
        for key, error in (
            ("invalid", "invalid_expected_fixture_key"), ("item_1", "evaluator_unavailable"),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(RunConflict, f"^{error}$"):
                control.execute("start", (key,), ())
        adapter.reset.assert_not_called()
        self.assertEqual(control.execute("start", (None,), ()), {"state": "Locate"})

    def test_terminal_delivery_verdict_requires_truth(self):
        for expected, delivered, outcome in (
            ("item_1", "item_1", "pass"),
            ("item_1", "item_2", "fail_wrong_object"),
            ("item_1", None, None),
            ("item_1", "invalid", None),
            ("item_1", TimeoutError(), None),
            (None, "item_1", None),
        ):
            with self.subTest(expected=expected, delivered=delivered):
                adapter = Mock()
                adapter.drop.return_value = {"success": True, "in_zone": True}
                if isinstance(delivered, Exception):
                    adapter.delivered_fixture_key.side_effect = delivered
                else:
                    adapter.delivered_fixture_key.return_value = delivered
                control = RunControl(adapter)
                control.execute("start", (expected,), ())
                self.assertEqual(
                    control.execute("drop", (), ("success", "in_zone")),
                    {"success": True, "in_zone": True},
                )
                verdict = {"state": "Done", "outcome": outcome}
                if outcome is None:
                    verdict["reason"] = "evaluator_unavailable"
                self.assertEqual(control.execute("finish", (), ()), verdict)
                self.assertEqual(control.execute("finish", (), ()), verdict)
                if expected is None:
                    adapter.delivered_fixture_key.assert_not_called()

    def test_turn_contact_and_mixed_recovery_exhaustion(self):
        adapter = Mock()
        adapter.turn.side_effect = [
            {"success": False}, {"success": False}, {"success": True}, {"success": False},
        ]
        adapter.move.return_value = {"success": False, "collided": True}
        control = RunControl(adapter)
        control.execute("start", (None,), ())

        for phase, retries in (("Blocked", 0), ("Blocked", 1), ("Blocked", 1)):
            control.execute("turn", (90,), ("success",))
            self.assertEqual((control.phase, control.retry_count), (phase, retries))
        control.execute("move", (-0.1,), ("success", "collided"))
        self.assertEqual((control.phase, control.retry_count), ("Blocked", 2))
        control.execute("turn", (90,), ("success",))
        self.assertEqual(control.retry_count, 3)
        self.assertEqual(
            control.execute("finish", (), ()), {"state": "Done", "outcome": "fail_collision"}
        )
        control.execute("reset", (), ())
        self.assertEqual((control.phase, control.retry_count), ("Idle", 0))

    def test_successful_move_recovers_from_turn_contact(self):
        adapter = Mock()
        adapter.turn.return_value = {"success": False}
        adapter.move.return_value = {"success": True, "collided": False}
        control = RunControl(adapter)
        control.execute("start", (None,), ())
        control.execute("turn", (90,), ("success",))
        control.execute("turn", (90,), ("success",))
        control.execute("move", (0.1,), ("success", "collided"))
        self.assertEqual((control.phase, control.retry_count), ("Go to object", 0))

    def test_drop_waits_for_poll_and_rejects_other_mutations(self):
        reading = Event()
        release_read = Event()
        adapter = Mock()
        adapter.drop.return_value = {"success": True, "in_zone": True}
        adapter.delivered_fixture_key.return_value = "item_1"

        def look():
            reading.set()
            if not release_read.wait(5):
                raise TimeoutError("Test did not release the camera read")
            return b"frame"

        adapter.look.side_effect = look
        control = RunControl(adapter)
        control.execute("start", ("item_1",), ())
        with ThreadPoolExecutor(max_workers=2) as pool:
            read = pool.submit(control.look)
            try:
                self.assertTrue(reading.wait(2))
                drop = pool.submit(control.execute, "drop", (), ("success", "in_zone"))
                deadline = monotonic() + 2
                while not control.mutation_lock.locked() and not drop.done() and monotonic() < deadline:
                    sleep(0.001)
                self.assertTrue(control.mutation_lock.locked())
                adapter.drop.assert_not_called()
                for method in ("reset", "finish"):
                    with self.assertRaisesRegex(RunConflict, "^mutation_in_progress$"):
                        control.execute(method, (), ())
            finally:
                release_read.set()
            self.assertEqual(read.result(timeout=2), b"frame")
            self.assertEqual(drop.result(timeout=2), {"success": True, "in_zone": True})

        adapter.drop.assert_called_once_with()
        adapter.reset.assert_called_once_with()
        self.assertEqual(control.execute("finish", (), ()), {"state": "Done", "outcome": "pass"})
