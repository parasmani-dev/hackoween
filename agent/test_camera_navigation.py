import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image,ImageDraw
from agent.camera_navigation import CameraNavigator,ground_point,components,waypoint,segment_clear


class CameraNavigationTests(unittest.TestCase):
    def test_projection_respects_clockwise_heading_and_right_pixel(self):
        state={'x':0,'y':0,'heading':0}
        center=ground_point(320,400,state); right=ground_point(480,400,state)
        self.assertGreater(center[0],0);self.assertAlmostEqual(center[1],0)
        self.assertLess(right[1],0)
        turned=ground_point(480,400,{**state,'heading':90})
        self.assertAlmostEqual(turned[0],right[1]);self.assertAlmostEqual(turned[1],-right[0])
        self.assertIsNone(ground_point(320,100,state))

    def test_teal_wall_is_not_green_floor_zone(self):
        image=Image.new('RGB',(640,480),(200,200,190));draw=ImageDraw.Draw(image)
        draw.rectangle((0,170,639,240),fill=(40,120,115))
        draw.polygon([(300,300),(450,300),(520,430),(250,430)],fill=(65,120,90))
        green=[c for c in components(image) if c['color']=='green']
        self.assertEqual(len(green),1);self.assertGreater(green[0]['box'][1],280)

    def test_can_exit_an_expanded_clearance_margin_but_not_cross_object(self):
        self.assertTrue(segment_clear((.4,0),(.6,0),[(0,0,.5)]))
        self.assertFalse(segment_clear((.4,0),(.2,0),[(0,0,.5)]))
        self.assertFalse(segment_clear((.4,0),(-.6,0),[(0,0,.5)]))

    def test_routes_around_obstacle_instead_of_straight_through(self):
        obstacles=[(1,0,.5)];start=(0,0);goal=(2,0)
        route=waypoint(start,goal,obstacles)
        self.assertIsNotNone(route);self.assertNotEqual(route,goal)
        self.assertTrue(segment_clear(start,route,obstacles))

    def test_drop_requires_reaching_camera_derived_goal_and_holding(self):
        with TemporaryDirectory() as root:
            p=Path(root)/'frame.png';Image.new('RGB',(640,480),(190,190,180)).save(p)
            nav=CameraNavigator('Fetch the red sphere and drop it in the green zone')
            nav.zone=(2,0);nav.zone_score=1
            state={'x':0,'y':0,'heading':0,'holding':True}
            self.assertEqual(nav.decide(p,state,None)[0],'move')
            self.assertEqual(nav.decide(p,{**state,'x':1.48},None)[0],'drop')
            self.assertNotEqual(nav.decide(p,{**state,'x':1.48,'holding':False},None)[0],'drop')

    def test_relational_command_does_not_get_simple_color_selector(self):
        nav=CameraNavigator('Fetch the blue cylinder beside the yellow cone and drop it in the green zone')
        self.assertIsNone(nav.task)
