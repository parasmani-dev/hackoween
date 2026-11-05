import unittest

from PIL import Image

from integration.camera_clearance import observed_clearance


class CameraClearanceTests(unittest.TestCase):
    def test_floor_projection_excludes_near_blind_spot_and_offscreen_cells(self):
        frame = Image.new("RGB", (640, 480), (158, 157, 155))
        clear = observed_clearance(
            frame, {(0, 0), (10, 0), (10, 50)}, cell_size=0.2, radius=0.35,
        )
        self.assertEqual(clear, {(10, 0)})

    def test_obstacle_in_footprint_excludes_cell(self):
        frame = Image.new("RGB", (640, 480), (158, 157, 155))
        frame.putpixel((320, 270), (83, 101, 125))
        self.assertEqual(
            observed_clearance(frame, {(10, 0)}, cell_size=0.2, radius=0.35), set(),
        )
