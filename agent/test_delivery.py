"""Camera-grounded delivery checks without simulator or provider calls."""

import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, ImageDraw

from agent.delivery import GreenDropZone


def pose(x=0.0, y=0.0, heading=0.0, holding=True, name="Carry"):
    return dict(x=x, y=y, heading=heading, holding=holding, state=name)


def forward(state, distance):
    angle = math.radians(state["heading"])
    return {**state, "x": state["x"] + distance * math.cos(angle),
            "y": state["y"] - distance * math.sin(angle)}


class GreenDropZoneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def frame(self, *, marked=True, occluded=False):
        image = Image.new("RGB", (640, 480), (199, 195, 184))
        draw = ImageDraw.Draw(image)
        if not occluded:
            # Synthetic floor geometry is projected with the public camera
            # calibration, independently of the detector's inverse transform.
            points = []
            for along, lateral in ((1.2, -0.65), (2.5, -0.65), (2.5, 0.65), (1.2, 0.65)):
                camera_forward = along - 0.22
                tilt = math.radians(10)
                z = camera_forward * math.cos(tilt) + 0.5 * math.sin(tilt)
                down = -camera_forward * math.sin(tilt) + 0.5 * math.cos(tilt)
                points.append((320 + 320 * lateral / z, 240 + 320 * down / z))
            draw.polygon(points, fill=(45, 135, 80))
            if marked:
                draw.line(points + points[:1], fill=(190, 225, 205), width=5)
        path = self.directory / f"frame-{len(list(self.directory.iterdir()))}.png"
        image.save(path)
        return path

    def test_release_point_must_be_inside_observed_floor_with_margin(self):
        zone = GreenDropZone("Fetch the sqaure and drop it in the green zone")
        initial = pose()
        self.assertTrue(zone.enabled)
        self.assertTrue(zone.observe(self.frame(), initial))
        self.assertTrue(zone.known)
        self.assertFalse(zone.ready(initial))
        self.assertEqual(zone.correction(initial), ("move", {"distance": 0.1}))
        self.assertFalse(zone.ready(forward(initial, 0.61)))
        self.assertTrue(zone.ready(forward(initial, 1.1)))
        self.assertIsNone(zone.correction(forward(initial, 1.1)))
        self.assertIn("do not pick up again", zone.guidance(initial))
        self.assertIn("bearing correction", zone.guidance(initial))

    def test_ground_map_uses_public_pose_and_survives_camera_occlusion(self):
        initial = pose(x=3.0, y=-4.0, heading=73.0)
        zone = GreenDropZone("Drop it in the green drop zone")
        self.assertTrue(zone.observe(self.frame(), initial))
        arrived = forward(initial, 1.1)
        self.assertFalse(zone.observe(self.frame(occluded=True), arrived))
        self.assertTrue(zone.known)
        self.assertTrue(zone.ready(arrived))
        self.assertFalse(zone.ready({**arrived, "holding": False}))
        self.assertFalse(zone.ready({**arrived, "state": "Blocked"}))
        self.assertIsNone(zone.correction({**initial, "state": "Blocked"}))

    def test_plain_green_object_and_teal_wall_do_not_establish_a_floor_zone(self):
        zone = GreenDropZone("Drop it in the green zone")
        self.assertFalse(zone.observe(self.frame(marked=False), pose()))
        wall = self.directory / "wall.png"
        Image.new("RGB", (640, 480), (48, 119, 115)).save(wall)
        self.assertFalse(zone.observe(wall, pose()))
        self.assertFalse(zone.known)
        self.assertFalse(zone.ready(pose()))
        self.assertIsNone(zone.correction(pose()))

    def test_only_explicit_green_zone_enables_this_calibration(self):
        for command in ("Fetch a green cube", "Drop in the blue zone", "Fetch square"):
            with self.subTest(command=command):
                zone = GreenDropZone(command)
                self.assertFalse(zone.enabled)
                self.assertFalse(zone.observe(self.frame(), pose()))
                self.assertFalse(zone.ready(pose()))
        zone = GreenDropZone("Drop in the green zone")
        self.assertTrue(zone.observe(self.frame(), pose(holding=False)))
        self.assertFalse(zone.ready(pose(holding=False)))

    def test_small_floor_fragment_before_pickup_is_search_only_until_full_reobservation(self):
        image = Image.new("RGB", (640, 480), (199, 195, 184))
        draw = ImageDraw.Draw(image)
        draw.rectangle((400, 220, 450, 232), fill=(45, 135, 80),
                       outline=(190, 225, 205), width=2)
        fragment = self.directory / "fragment.png"
        image.save(fragment)
        zone = GreenDropZone("Fetch yellow cone and drop it in the green zone")
        self.assertTrue(zone.observe(fragment, pose(holding=False, name="Locate")))
        self.assertTrue(zone.search_known)
        self.assertFalse(zone.known)
        self.assertFalse(zone.ready(pose()))
        self.assertIsNone(zone.correction(pose()))
        arrival = zone._arrival(pose())
        self.assertGreater(arrival[0], 2)
        self.assertGreater(arrival[1], 0)
        self.assertIn("only as a search direction", zone.guidance(pose()))
        self.assertIn("before dropping", zone.guidance(pose()))
        self.assertFalse(zone.observe(self.frame(occluded=True), pose()))
        self.assertEqual(zone._arrival(pose()), arrival)
        self.assertTrue(zone.observe(self.frame(), pose()))
        self.assertTrue(zone.known)
        self.assertTrue(zone.ready(forward(pose(), 1.1)))

    def test_ambiguous_small_green_fragments_do_not_choose_a_search_destination(self):
        image = Image.new("RGB", (640, 480), (199, 195, 184))
        draw = ImageDraw.Draw(image)
        for left in (160, 400):
            draw.rectangle((left, 220, left + 50, 232), fill=(45, 135, 80),
                           outline=(190, 225, 205), width=2)
        path = self.directory / "ambiguous-fragments.png"
        image.save(path)
        zone = GreenDropZone("Drop in the green zone")
        self.assertFalse(zone.observe(path, pose(holding=False)))
        self.assertFalse(zone.search_known)
        self.assertFalse(zone.known)

    def test_correction_turn_and_move_are_bounded_and_never_release(self):
        zone = GreenDropZone("Drop in the green zone")
        self.assertTrue(zone.observe(self.frame(), pose()))
        name, args = zone.correction(pose(heading=-50))
        self.assertEqual(name, "turn")
        self.assertEqual(args, {"degrees": 25.0})
        name, args = zone.correction(pose())
        self.assertEqual(name, "move")
        self.assertLessEqual(abs(args["distance"]), 0.1)
        self.assertIsNone(zone.correction(pose(x=-5)))


if __name__ == "__main__":
    unittest.main()
