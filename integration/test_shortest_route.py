import math
import unittest

from integration.shortest_route import shortest_route


class ShortestRouteTests(unittest.TestCase):
    def test_uses_direct_diagonal_and_nearest_goal(self):
        clear = {(x, y) for x in range(4) for y in range(4)}
        self.assertEqual(shortest_route((0, 0), {(3, 3), (2, 2)}, clear),
                         [(0, 0), (1, 1), (2, 2)])
        self.assertEqual(shortest_route((0, 0), {(0, 0)}, clear), [(0, 0)])

    def test_takes_shorter_side_of_wall(self):
        clear = {(x, y) for x in range(5) for y in range(5)}
        clear.difference_update({(2, 0), (2, 1), (2, 2)})
        route = shortest_route((0, 1), {(4, 1)}, clear)
        self.assertIn((2, 3), route)
        self.assertAlmostEqual(
            sum(math.dist(a, b) for a, b in zip(route, route[1:])),
            4 + 2 * math.sqrt(2),
        )

    def test_replans_when_payload_clearance_closes_gap(self):
        clear = {(x, y) for x in range(5) for y in range(3)}
        start, goal = (0, 1), (4, 1)
        self.assertIn((2, 1), shortest_route(start, {goal}, clear))
        clear.remove((2, 1))
        self.assertNotIn((2, 1), shortest_route(start, {goal}, clear))
        clear.difference_update({(2, 0), (2, 2)})
        self.assertIsNone(shortest_route(start, {goal}, clear))

    def test_does_not_cut_unknown_corner(self):
        self.assertIsNone(shortest_route((0, 0), {(1, 1)}, {(0, 0), (1, 1)}))
        self.assertIsNone(shortest_route((0, 0), {(1, 0)}, {(0, 0)}))
