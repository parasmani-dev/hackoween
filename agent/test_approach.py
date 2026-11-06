"""Synthetic camera/odometry regressions; no simulator or provider calls."""

import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from agent.approach import FloorApproach


def pose(x=0.0, y=0.0, heading=0.0):
    return {"x": x, "y": y, "heading": heading, "holding": False}


class FloorApproachTests(unittest.TestCase):
    def test_round_target_reorientation_preserves_ground_point_and_motor_yaw_margin(self):
        approach = FloorApproach(max_heading_change=15)
        self.assertTrue(approach.observe(self.frame(), pose(), "visible"))
        ground = approach._ground
        clipped = self.frame(clipped=True)
        self.assertTrue(approach.reorient(clipped, pose(x=.5, heading=13)))
        self.assertEqual(approach._ground, ground)
        self.assertIsNotNone(approach.distance(clipped, pose(x=.57, heading=17)))
        self.assertFalse(approach.reorient(self.frame(clipped=True, color=(30, 30, 230)),
                                         pose(x=.57, heading=17)))

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.index = 0

    def frame(self, *, clipped=False, color=(230, 30, 30), box=None,
              ambiguous=False, occluded=False):
        image = Image.new("RGB", (160, 120), (110, 110, 110))
        draw = ImageDraw.Draw(image)
        bounds = box or ((52, 60, 108, 130) if clipped else (65, 54, 95, 87))
        if ambiguous:
            draw.rectangle((45, 60, 73, 130), fill=color)
            draw.rectangle((87, 60, 115, 130), fill=(30, 30, 230))
        else:
            draw.rectangle(bounds, fill=color)
        if occluded:
            draw.rectangle((45, 94, 115, 120), fill=(110, 110, 110))
        path = self.directory / f"{self.index:04d}.png"
        self.index += 1
        image.save(path)
        return path

    @staticmethod
    def initial_contact():
        # Bottom boundary of the synthetic visible component is y=88.
        return 0.22 + 0.5 / math.tan(math.radians(10) + math.atan((88 - 60) / 80))

    def test_contact_anchor_allows_bigger_steps_then_returns_to_tiny_probes(self):
        approach = FloorApproach()
        self.assertTrue(approach.observe(self.frame(), pose(), "visible"))
        clipped = self.frame(clipped=True)
        far_step = approach.distance(clipped, pose(x=0.50))
        self.assertGreater(far_step, 0.07)
        self.assertLessEqual(far_step, 0.10)
        camera_range = self.initial_contact() - 0.50 - 0.22
        self.assertLess(camera_range / (camera_range - far_step), 1.35)
        near_x = self.initial_contact() - 0.39
        self.assertAlmostEqual(approach.distance(clipped, pose(x=near_x)), 0.02)
        # The ground point remains fixed, rather than being reset by each move.
        tight_x = self.initial_contact() - 0.33
        self.assertAlmostEqual(approach.distance(clipped, pose(x=tight_x)), 0.01)
        stop_x = self.initial_contact() - 0.323
        self.assertEqual(approach.distance(clipped, pose(x=stop_x)), 0.0)

    def test_projection_uses_clockwise_heading_and_public_pose(self):
        approach = FloorApproach()
        self.assertTrue(approach.observe(self.frame(), pose(x=2.0, y=3.0, heading=90.0), "visible"))
        clipped = self.frame(clipped=True)
        self.assertGreater(approach.distance(clipped, pose(x=2.0, y=2.5, heading=90.0)), 0.07)
        near_y = 3.0 - (self.initial_contact() - 0.39)
        self.assertAlmostEqual(approach.distance(clipped, pose(x=2.0, y=near_y, heading=90.0)), 0.02)

    def test_only_explicit_visible_base_can_create_an_anchor(self):
        for status in ("clipped", "unknown", None):
            with self.subTest(status=status):
                approach = FloorApproach()
                self.assertFalse(approach.observe(self.frame(), pose(), status))
                self.assertIsNone(approach.distance(self.frame(clipped=True), pose(x=0.5)))
        for box in ((0, 45, 159, 85), (0, 56, 60, 85), (65, 54, 95, 130)):
            self.assertFalse(FloorApproach().observe(self.frame(box=box), pose(), "visible"))

    def test_ambiguous_visible_components_and_unusable_poses_are_rejected(self):
        # Two substantial nonoverlapping components straddle the center.
        ambiguous = Image.new("RGB", (160, 120), (110, 110, 110))
        draw = ImageDraw.Draw(ambiguous)
        draw.rectangle((45, 55, 73, 90), fill=(230, 30, 30))
        draw.rectangle((87, 55, 115, 90), fill=(30, 30, 230))
        path = self.directory / "ambiguous.png"
        ambiguous.save(path)
        self.assertFalse(FloorApproach().observe(path, pose(), "visible"))
        for field in ("x", "y", "heading"):
            for value in (True, "0", float("nan"), float("inf")):
                state = pose()
                state[field] = value
                self.assertFalse(FloorApproach().observe(self.frame(), state, "visible"))

    def test_loss_occlusion_color_swap_and_sudden_size_change_invalidate(self):
        replacements = (
            self.frame(clipped=True, occluded=True),
            self.frame(clipped=True, color=(30, 30, 230)),
            self.frame(clipped=True, ambiguous=True),
            self.frame(box=(10, 20, 150, 130)),
            self.directory / "missing.png",
        )
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                approach = FloorApproach()
                self.assertTrue(approach.observe(self.frame(), pose(), "visible"))
                self.assertIsNone(approach.distance(replacement, pose(x=0.5)))
                self.assertIsNone(approach.distance(self.frame(clipped=True), pose(x=0.5)))

    def test_turn_sideways_retreat_and_large_pose_jumps_invalidate(self):
        for state in (pose(x=0.5, heading=4.0), pose(x=0.5, y=0.07),
                      pose(x=-0.04), pose(x=0.61), pose(x=2.0),
                      {**pose(x=0.5), "holding": True}):
            with self.subTest(state=state):
                approach = FloorApproach()
                self.assertTrue(approach.observe(self.frame(), pose(), "visible"))
                self.assertIsNone(approach.distance(self.frame(clipped=True), state))

    def test_reset_discards_anchor_and_clipped_reports_cannot_replace_it(self):
        approach = FloorApproach()
        self.assertTrue(approach.observe(self.frame(), pose(), "visible"))
        self.assertFalse(approach.observe(self.frame(clipped=True), pose(x=0.5), "clipped"))
        self.assertGreater(approach.distance(self.frame(clipped=True), pose(x=0.5)), 0.07)
        approach.reset()
        self.assertIsNone(approach.distance(self.frame(clipped=True), pose(x=0.5)))

    def test_explicit_symmetric_profile_retains_anchor_after_small_alignment(self):
        visible = self.frame(box=(80, 54, 110, 87))
        clipped = self.frame(clipped=True)
        angle = math.radians(13)
        aligned = pose(x=0.5 * math.cos(angle), y=-0.5 * math.sin(angle), heading=13.0)
        approach = FloorApproach(max_heading_change=15.0)
        self.assertTrue(approach.observe(visible, pose(), "visible"))
        distance = approach.distance(clipped, aligned)
        self.assertIsNotNone(distance)
        self.assertGreater(distance, 0.02)
        self.assertLessEqual(distance, 0.10)
        self.assertIsNone(approach.distance(clipped, {**aligned, "heading": 16.0}))
        # The default profile keeps its existing conservative heading limit.
        approach = FloorApproach()
        self.assertTrue(approach.observe(visible, pose(), "visible"))
        self.assertIsNone(approach.distance(clipped, aligned))


if __name__ == "__main__":
    unittest.main()
