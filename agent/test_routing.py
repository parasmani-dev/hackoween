"""Synthetic camera-only obstacle map and detour regressions."""

import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from agent.routing import CameraRoute


def pose(x=0.0, y=0.0, heading=0.0):
    return {"x": x, "y": y, "heading": heading}


class CameraRouteTests(unittest.TestCase):
    def test_scanned_neighborhood_allows_only_short_rear_clearance_escape(self):
        route = CameraRoute()
        route._poses.extend(((-.10, 0.0, 0.0), (0.0, 0.0, 0.0)))
        self.assertEqual(route._retreat((0.0, 0.0, -100.0)), ("move", {"distance": -.10}))
        behind = route._world(-.30, 0.0, (0.0, 0.0, -100.0))
        route._points[(0, 0)] = behind
        self.assertIsNone(route._retreat((0.0, 0.0, -100.0)))

    def test_wall_updates_cannot_erase_an_occluded_foreground_object(self):
        image = Image.new("RGB", (160, 120), (208, 204, 194))
        ImageDraw.Draw(image).rectangle((65, 55, 95, 90), fill=(30, 50, 220))
        path = self.root / "blue-object.png"
        image.save(path)
        route = CameraRoute("pick yellow cone")
        route.observe(path, pose())
        self.assertTrue(route._objects)
        for index in range(100):
            route._points[(1000 + index, 0)] = (-2.0, index / 100)
        route.observe(self.frame("occluded", panel=False), pose())
        self.assertIsNotNone(route.propose("move", {"distance": .5},
                                          {**pose(), "holding": True}))
        self.assertLessEqual(sum(len(key) == 2 for key in route._points), 64)
        self.assertLessEqual(len(route._points), 128)

    def test_search_inspects_the_nearest_camera_observed_occluding_edge(self):
        route = CameraRoute()
        self.assertIsNone(route.search_turn(pose()))
        route._points[(0, 0)] = (.8, .35)
        self.assertEqual(route.search_turn(pose()), -45)
        route._points.clear()
        route._points[(0, 0)] = (.8, -.35)
        self.assertEqual(route.search_turn(pose()), 45)

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def frame(self, name="panel", *, panel=True, warm=False):
        image = Image.new("RGB", (160, 120), (208, 204, 194))
        if panel:
            ImageDraw.Draw(image).polygon(((108, 0), (159, 0), (159, 115), (108, 100)),
                                          fill=(130, 110, 90) if warm else (100, 119, 138))
        path = self.root / f"{name}.png"
        image.save(path)
        return path

    def test_camera_ground_edges_remain_after_panel_leaves_view(self):
        route = CameraRoute()
        self.assertTrue(route.observe(self.frame(), pose()))
        count = len(route._points)
        self.assertGreater(count, 3)
        route.observe(self.frame("empty", panel=False), pose(x=0.10))
        self.assertEqual(len(route._points), count)
        self.assertIsNotNone(route.propose("move", {"distance": 0.5}, pose(x=0.10)))

    def test_side_panel_blocks_whole_body_even_with_center_clear(self):
        route = CameraRoute()
        route._points[(0, 0)] = (0.60, -0.28)
        self.assertEqual(route.propose("move", {"distance": 0.5}, pose()),
                         ("turn", {"degrees": -45}))

    def test_detour_latches_until_measured_side_and_forward_progress(self):
        route = CameraRoute()
        route._points[(0, 0)] = (0.85, -0.28)
        self.assertEqual(route.propose("move", {"distance": 0.7}, pose())[0], "turn")
        self.assertEqual(route.propose("turn", {"degrees": 45}, pose(heading=-45)),
                         ("move", {"distance": 0.2}))
        # Moving only forward on the old route does not establish the detour.
        self.assertIsNotNone(route.propose("turn", {"degrees": 45}, pose(x=0.35, heading=-45)))
        self.assertIsNotNone(route._origin)
        self.assertIsNone(route.propose("pickup", {}, pose(x=0.45, y=0.45, heading=-45)))
        route.propose("turn", {"degrees": 45}, pose(x=0.45, y=0.45, heading=-45))
        self.assertIsNone(route._origin)

    def test_segments_block_between_sparse_points_and_heading_wraps(self):
        route = CameraRoute()
        route._points[(1, 0)], route._points[(2, 0)] = (0.7, -0.4), (0.7, 0.4)
        route._edges.add(((1, 0), (2, 0)))
        self.assertFalse(route._clear((0, 0, 360), 0.5))
        self.assertTrue(route._clear((0, 0, 180), 0.2))

    def test_close_obstacle_retreat_requires_actual_observed_path(self):
        route = CameraRoute()
        route._points[(0, 0)] = (0.36, 0.10)
        route._poses.extend(((0.0, 0.0, 0.0), (0.2, 0.0, 0.0)))
        self.assertEqual(route.propose("move", {"distance": 0.3}, pose(x=0.2)),
                         ("move", {"distance": -0.15}))
        route = CameraRoute()
        route._points[(0, 0)] = (0.36, 0.10)
        self.assertEqual(route.propose("move", {"distance": 0.3}, pose()),
                         ("move", {"distance": 0.0}))

    def test_warm_floor_and_unreadable_images_do_not_create_obstacles(self):
        route = CameraRoute()
        self.assertFalse(route.observe(self.frame("warm", warm=True), pose()))
        self.assertFalse(route.observe(self.root / "missing.png", pose()))
        self.assertIsNone(route.propose("move", {"distance": 0.5}, pose()))
        self.assertFalse(route.observe(self.frame(), pose(heading=float("nan"))))

    def test_carried_object_blocks_travel_before_the_chassis_reaches_panel(self):
        route = CameraRoute()
        route._points[(0, 0)] = (0.95, -0.10)
        self.assertIsNone(route.propose("move", {"distance": 0.2}, pose()))
        self.assertEqual(route.propose("move", {"distance": 0.2}, {**pose(), "holding": True}),
                         ("turn", {"degrees": -45}))

    def test_carried_turn_checks_the_arc_and_allows_turning_away(self):
        route = CameraRoute()
        route._points[(0, 0)] = (0.65, -0.28)
        state = {**pose(), "holding": True}
        self.assertIsNone(route.propose("turn", {"degrees": -45}, state))
        self.assertEqual(route.propose("turn", {"degrees": 45}, state),
                         ("turn", {"degrees": -45}))
        # The next proposal cannot immediately undo the escape turn; take
        # bounded translation on the clear side before resuming the scan.
        self.assertEqual(route.propose("turn", {"degrees": 45},
                                       {**state, "heading": -45.0}),
                         ("move", {"distance": 0.2}))
        # An edge can cross the held-object arc even when both ends are clear.
        route._points[(1, 0)], route._points[(2, 0)] = (0.85, -0.5), (0.45, -0.5)
        route._edges.add(((1, 0), (2, 0)))
        self.assertFalse(route._turn_clear((0, 0, 0), 90))

    def colored_frame(self):
        image = Image.new("RGB", (160, 120), (208, 204, 194))
        draw = ImageDraw.Draw(image)
        draw.ellipse((82, 62, 111, 91), fill=(225, 28, 28))
        draw.polygon(((50, 48), (39, 91), (66, 91)), fill=(230, 190, 20))
        path = self.root / "colored.png"
        image.save(path)
        return path

    def test_colored_foreground_is_mapped_but_command_target_is_excluded(self):
        route = CameraRoute("Fetch the yellow cone and drop it in the green zone")
        self.assertTrue(route.observe(self.colored_frame(), pose()))
        self.assertEqual(len(route._objects), 1)
        self.assertLess(route._objects[0]["hue"], 15)
        self.assertEqual(len(route._points), 8)
        self.assertEqual(len(route._edges), 8)

    def test_colored_obstacle_memory_moves_with_public_odometry_and_steers_away(self):
        route = CameraRoute("Fetch the yellow cone")
        route.observe(self.colored_frame(), pose())
        remembered = list(route._points.values())
        route.observe(self.frame("empty-colored", panel=False), pose(x=0.25))
        self.assertEqual(list(route._points.values()), remembered)
        self.assertEqual(route.propose("move", {"distance": 0.5}, pose(x=0.25)),
                         ("turn", {"degrees": -45}))

    def test_held_target_color_stays_excluded_from_the_obstacle_map(self):
        route = CameraRoute("Pick up the red sphere and drop it in the green zone")
        route.observe(self.colored_frame(), {**pose(), "holding": True})
        self.assertEqual(len(route._objects), 1)
        self.assertGreater(route._objects[0]["hue"], 22)
        self.assertLess(route._objects[0]["hue"], 45)

    def test_short_teal_wall_floor_edge_is_retained_when_it_leaves_view(self):
        image = Image.new("RGB", (160, 120), (208, 204, 194))
        ImageDraw.Draw(image).rectangle((0, 54, 159, 76), fill=(42, 116, 113))
        path = self.root / "teal-wall.png"
        image.save(path)
        route = CameraRoute("Fetch the yellow cone and drop it in the green zone")
        self.assertTrue(route.observe(path, pose()))
        self.assertGreater(len(route._points), 10)
        # The short stripe would fail the neutral-panel 36-pixel height rule.
        remembered = list(route._points.values())
        route.observe(self.frame("hidden-wall", panel=False), pose(x=0.20))
        self.assertEqual(list(route._points.values()), remembered)
        self.assertIsNotNone(route.propose("move", {"distance": 0.40},
                                          {**pose(x=0.20), "holding": True}))

    def test_teal_wall_projection_is_pulled_closer_for_baseboard_uncertainty(self):
        image = Image.new("RGB", (160, 120), (208, 204, 194))
        ImageDraw.Draw(image).rectangle((0, 54, 159, 76), fill=(42, 116, 113))
        path = self.root / "teal-range.png"
        image.save(path)
        route = CameraRoute("Fetch the yellow cone")
        route.observe(path, pose())
        nominal = 0.22 + 0.5 / math.tan(math.radians(10) + math.atan((77 - 60) / 80))
        for point in route._points.values():
            self.assertAlmostEqual(point[0], 0.22 + 0.8 * (nominal - 0.22))


if __name__ == "__main__":
    unittest.main()
