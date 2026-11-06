"""Escape-angle regressions for the recorded yellow-cone recovery deadlock."""

import math
import unittest

from agent.navigation import NavigationMemory


def pose(heading=0.0, x=0.0, y=0.0):
    return dict(state="Blocked", x=x, y=y, heading=heading, holding=False, retry_count=0)


def translated(state, distance):
    angle = math.radians(state["heading"])
    return {**state, "x": state["x"] + distance * math.cos(angle),
            "y": state["y"] - distance * math.sin(angle)}


class RecoveryEscapeTests(unittest.TestCase):
    def collision(self, before=None, after=None):
        before = pose() if before is None else before
        after = before if after is None else after
        memory = NavigationMemory()
        memory.observe("move", {"distance": 0.5}, {"success": False, "collided": True},
                       before, after, "0000.png")
        return memory

    def test_failed_yellow_trace_gets_same_side_escape_before_retrying_corridor(self):
        # Public headings from the failed run: retreat, -8 degree correction,
        # and a later -20 degree turn never left the 25 degree collision fan.
        before = pose(-77.1573469041105)
        contact = pose(-73.68577838915883)
        memory = self.collision(before, contact)
        retreat = translated(contact, -0.3)
        memory.observe("move", {"distance": -0.3}, {"success": True, "collided": False},
                       contact, retreat, "0001.png")
        small_turn = {**retreat, "heading": -80.46427174065268}
        memory.observe("turn", {"degrees": -8}, {"success": True},
                       retreat, small_turn, "0002.png")
        self.assertEqual(memory.detour_turn_sign, -1)
        self.assertEqual(memory.rejection("move", {"distance": 0.3}, small_turn, "0003.png"),
                         "previously_blocked_approach")
        self.assertEqual(memory.escape_turn(small_turn), -25.0)

        partial_turn = {**small_turn, "heading": -99.96735941425565}
        memory.observe("turn", {"degrees": -20}, {"success": True},
                       small_turn, partial_turn, "0003.png")
        self.assertEqual(memory.rejection("turn", {"degrees": 30}, partial_turn, "0004.png"),
                         "recovery_requires_translation")
        self.assertEqual(memory.escape_turn(partial_turn), -25.0)
        self.assertIn("-25.0 degrees", memory.recovery_guidance(partial_turn))
        self.assertIn("55 degrees", memory.recovery_guidance(partial_turn))

        current = partial_turn
        for _ in range(3):
            turn = memory.escape_turn(current)
            if turn is None:
                break
            self.assertLess(turn, -2)
            self.assertLessEqual(abs(turn), 25)
            self.assertIsNone(memory.rejection("turn", {"degrees": turn}, current, "0004.png"))
            following = {**current, "heading": current["heading"] + turn}
            memory.observe("turn", {"degrees": turn}, {"success": True},
                           current, following, "0004.png")
            current = following
        self.assertIsNone(memory.escape_turn(current))
        self.assertGreaterEqual(abs(math.remainder(current["heading"] - before["heading"], 360)), 55)
        self.assertIsNone(memory.rejection("move", {"distance": 0.2}, current, "0005.png"))
        self.assertTrue(memory.repositioning)
        self.assertTrue(memory.collisions[-1]["active"])
        for _ in range(2):
            following = translated(current, 0.2)
            memory.observe("move", {"distance": 0.2}, {"success": True, "collided": False},
                           current, following, "0005.png")
            current = following
        self.assertFalse(memory.repositioning)
        self.assertFalse(memory.collisions[-1]["active"])

    def test_safe_reverse_remains_available_without_erasing_the_collision_ban(self):
        memory = self.collision()
        initial = pose()
        turned = pose(8)
        memory.observe("turn", {"degrees": 8}, {"success": True}, initial, turned, "0001.png")
        self.assertEqual(memory.detour_turn_sign, 1)
        self.assertIsNone(memory.rejection("move", {"distance": -0.3}, turned, "0002.png"))
        retreat = translated(turned, -0.3)
        memory.observe("move", {"distance": -0.3}, {"success": True, "collided": False},
                       turned, retreat, "0002.png")
        self.assertTrue(memory.repositioning)
        self.assertTrue(memory.collisions[-1]["active"])
        self.assertEqual(memory.rejection("move", {"distance": 0.3}, retreat, "0003.png"),
                         "previously_blocked_approach")

    def test_wraparound_keeps_chosen_scan_direction_and_requires_real_rotation(self):
        initial = pose(179)
        memory = self.collision(initial)
        memory.observe("turn", {"degrees": 8}, {"success": True}, initial, initial, "0001.png")
        self.assertIsNone(memory.detour_turn_sign)
        self.assertIsNone(memory.escape_turn(initial))
        turned = pose(-173)
        memory.observe("turn", {"degrees": 8}, {"success": True}, initial, turned, "0002.png")
        self.assertEqual(memory.escape_turn(turned), 25)
        self.assertEqual(memory.rejection("turn", {"degrees": -25}, turned, "0003.png"),
                         "recovery_requires_translation")
        beyond_fan = pose(-126)
        self.assertIsNone(memory.escape_turn(beyond_fan))
        self.assertIn("0.30 m", memory.recovery_guidance(beyond_fan))
        near_escape = pose(-127)
        self.assertEqual(memory.escape_turn(near_escape), 3)
        self.assertIsNone(memory.rejection("turn", {"degrees": 3}, near_escape, "0004.png"))

    def test_escape_never_chooses_a_side_before_camera_directed_motion(self):
        memory = self.collision()
        self.assertIsNone(memory.escape_turn(pose()))
        self.assertIn("no chosen side", memory.recovery_guidance(pose()))
        self.assertIsNone(NavigationMemory().escape_turn(pose()))
        self.assertEqual(NavigationMemory().recovery_guidance(pose()), "")


if __name__ == "__main__":
    unittest.main()
