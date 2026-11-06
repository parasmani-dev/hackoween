"""Operator-only scene geometry checks; never used by the vision agent."""

import json
import math
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parent


class LayoutClearanceTests(unittest.TestCase):
    def test_cue_pairs_allow_rover_turning_and_preserve_judge_swap(self):
        layouts = json.loads((ROOT / "layouts.json").read_text())
        # A robot at pickup reach must have room to rotate beside the cue.
        minimum = 0.5 + math.hypot(0.52 / 2, 0.46 / 2) + math.hypot(0.18, 0.18) + 0.1
        for name, layout in layouts.items():
            with self.subTest(layout=name):
                positions = layout["objects"]
                nearest = {}
                for cue in ("ITEM_2", "ITEM_5"):
                    distances = {cylinder: math.dist(positions[cue], positions[cylinder])
                                 for cylinder in ("ITEM_3", "ITEM_4")}
                    self.assertGreaterEqual(min(distances.values()), minimum)
                    nearest[cue] = min(distances, key=distances.get)
                self.assertNotEqual(nearest["ITEM_2"], nearest["ITEM_5"])
                self.assertEqual(nearest["ITEM_5"].lower(), layout["expected"])
        self.assertNotEqual(layouts["A"]["expected"], layouts["B"]["expected"])

    def test_standalone_world_has_distinct_cylinders_and_matches_layout_a(self):
        layout = json.loads((ROOT / "layouts.json").read_text())["A"]
        world = (ROOT / "worlds/scenario-2.wbt").read_text()
        positions = {}
        for name, expected in layout["objects"].items():
            match = re.search(r"DEF " + name + r" Solid\s*\{\s*translation\s+([^\n]+)", world)
            positions[name] = [float(value) for value in match[1].split()[:2]]
            self.assertEqual(positions[name], expected)
        self.assertNotEqual(positions["ITEM_3"], positions["ITEM_4"])


if __name__ == "__main__":
    unittest.main()
