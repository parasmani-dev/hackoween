"""Synthetic visual continuity checks; no private fixtures or model calls."""

import colorsys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from agent.tracking import ApproachTracker


class ApproachTrackerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.index = 0

    @staticmethod
    def color(hue):
        return tuple(round(value * 255) for value in colorsys.hsv_to_rgb(hue / 360, 0.95, 0.95))

    def frame(self, hue=240, shape="rectangle", growth=False, gray=False, occlude=False,
              box=None, distractor=False, ambiguous=False):
        image = Image.new("RGB", (320, 240), (100, 100, 100))
        draw = ImageDraw.Draw(image)
        color = self.color(hue)
        if not gray:
            bounds = box or ((84, 124, 236, 290) if growth else (90, 130, 230, 280))
            if ambiguous:
                draw.rectangle((70, 130, 150, 280), fill=color)
                draw.rectangle((170, 130, 250, 280), fill=self.color((hue + 60) % 360))
            elif shape == "ellipse":
                draw.ellipse(bounds, fill=color)
            elif shape == "triangle":
                left, top, right, bottom = bounds
                draw.polygon(((left + right) // 2, top, right, bottom, left, bottom), fill=color)
            else:
                draw.rectangle(bounds, fill=color)
            if occlude:
                draw.rectangle((85, 120, 240, 220), fill=(100, 100, 100))
            if distractor:
                draw.rectangle((270, 145, 319, 280), fill=color)
        path = self.directory / f"{self.index:04d}.png"
        self.index += 1
        image.save(path)
        return path

    def test_acquires_and_tracks_small_growth_for_six_hues_and_three_shapes(self):
        for hue in (0, 60, 120, 180, 240, 300):
            for shape in ("rectangle", "ellipse", "triangle"):
                with self.subTest(hue=hue, shape=shape):
                    tracker = ApproachTracker()
                    self.assertTrue(tracker.start(self.frame(hue, shape)))
                    self.assertTrue(tracker.update(self.frame(hue, shape, growth=True)))
                    self.assertTrue(tracker.active)

    def test_red_wrap_and_small_lighting_hue_change_remain_associated(self):
        tracker = ApproachTracker()
        self.assertTrue(tracker.start(self.frame(359)))
        self.assertTrue(tracker.update(self.frame(1, growth=True)))
        tracker = ApproachTracker()
        self.assertTrue(tracker.start(self.frame(240)))
        self.assertTrue(tracker.update(self.frame(245, growth=True)))

    def test_lost_occluded_or_color_changed_components_deactivate(self):
        for replacement in (self.frame(gray=True), self.frame(occlude=True), self.frame(120)):
            with self.subTest(replacement=replacement):
                tracker = ApproachTracker()
                self.assertTrue(tracker.start(self.frame()))
                self.assertFalse(tracker.update(replacement))
                self.assertFalse(tracker.active)
                self.assertFalse(tracker.update(self.frame()))

    def test_does_not_jump_to_same_color_distractor(self):
        tracker = ApproachTracker()
        self.assertTrue(tracker.start(self.frame(distractor=True)))
        replacement = self.frame(box=(240, 130, 319, 280))
        self.assertFalse(tracker.update(replacement))
        self.assertFalse(tracker.active)

    def test_ambiguous_acquisition_and_sudden_scale_change_are_rejected(self):
        tracker = ApproachTracker()
        self.assertFalse(tracker.start(self.frame(ambiguous=True)))
        self.assertTrue(tracker.start(self.frame()))
        self.assertFalse(tracker.update(self.frame(box=(20, 20, 300, 280))))

    def test_split_or_ambiguous_replacement_does_not_reacquire(self):
        tracker = ApproachTracker()
        self.assertTrue(tracker.start(self.frame()))
        self.assertFalse(tracker.update(self.frame(ambiguous=True)))
        self.assertFalse(tracker.active)

    def test_gray_unclipped_upper_and_unreadable_frames_have_no_target(self):
        for path in (self.frame(gray=True), self.frame(box=(90, 20, 230, 160)),
                     self.directory / "missing.png"):
            with self.subTest(path=path):
                tracker = ApproachTracker()
                self.assertFalse(tracker.start(path))
                self.assertFalse(tracker.active)

    def test_reset_requires_explicit_new_acquisition(self):
        tracker = ApproachTracker()
        frame = self.frame()
        self.assertTrue(tracker.start(frame))
        tracker.reset()
        self.assertFalse(tracker.active)
        self.assertFalse(tracker.update(frame))
        self.assertTrue(tracker.start(frame))
