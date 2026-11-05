import unittest
from agent.claude import Decision
from agent.tools import extract_target_view, tool_definitions
from agent.visual_control import correct_action


class VisualControlTests(unittest.TestCase):
    def test_strict_schema_avoids_unsupported_provider_constraints(self):
        import json
        schema = json.dumps(tool_definitions(visual_memory=True))
        self.assertNotIn('minItems', schema)
        self.assertNotIn('maxItems', schema)

    def proposal(self, name='pickup', args=None, box=None, floor=False, clear=True):
        return Decision(name, args or {}, {}, '',
                        {'box': box, 'path_clear': clear, 'floor_zone': floor})

    def test_visible_left_target_is_aligned_before_forward_or_pickup(self):
        for name,args in [('move', {'distance': .3}), ('pickup', {})]:
            actual,reason = correct_action(self.proposal(name,args,[70,250,230,430]),
                                          holding=False,depth=4.7)
            self.assertEqual(actual.name,'turn')
            self.assertLess(actual.args['degrees'],0)
            self.assertEqual(reason,'align_to_reported_target')

    def test_far_ray_does_not_mean_clipped_object_is_within_reach(self):
        actual,_=correct_action(self.proposal(box=[220,300,420,480]),holding=False,depth=3.5)
        self.assertEqual((actual.name,actual.args),('move',{'distance':.05}))

    def test_failed_trace_depths_request_approach_instead_of_pickup(self):
        for depth in (.234,.169,.116):
            actual,_=correct_action(self.proposal(box=[220,300,420,480]),holding=False,depth=depth,disabled=['pickup'])
            self.assertEqual(actual.name,'move')
            self.assertGreater(actual.args['distance'],0)
            self.assertLessEqual(actual.args['distance'],depth-.04)

    def test_near_target_is_picked_up_before_another_drive(self):
        actual,_=correct_action(self.proposal('move',{'distance':.1},[220,300,420,480]),
                               holding=False,depth=.06)
        self.assertEqual(actual.name,'pickup')

    def test_near_clipped_target_uses_pickup_tolerance_before_alignment(self):
        # Exact live decision 12: correcting this box caused turn contact.
        for name,args in [('pickup',{}),('move',{'distance':.05})]:
            actual,_=correct_action(self.proposal(name,args,[0,200,480,480]),
                                   holding=False,depth=.064)
            self.assertEqual(actual.name,'pickup')

    def test_right_target_cannot_trigger_left_alignment_turn(self):
        actual,reason=correct_action(self.proposal('turn',{'degrees':-30},[520,380,640,480]),
                                    holding=False,depth=3)
        self.assertGreater(actual.args['degrees'],0)
        self.assertEqual(reason,'turn_toward_visible_target')

    def test_missing_target_with_distant_range_requires_reacquisition(self):
        actual,_=correct_action(self.proposal('move',{'distance':.2}),holding=False,depth=3)
        self.assertEqual(actual.name,'turn')

    def test_search_does_not_immediately_reverse_direction(self):
        history=[{'tool':'turn','args':{'degrees':45},'result':{'success':True}}]
        actual,_=correct_action(self.proposal('turn',{'degrees':-45}),holding=False,
                               depth=3,history=history)
        self.assertEqual(actual.args,{'degrees':45})

    def test_full_delivery_scan_repositions_instead_of_turn_deadlock(self):
        history=[{'tool':'turn','args':{'degrees':45},'result':{'success':True}}] * 8
        for name,args in [('move',{'distance':.2}),('turn',{'degrees':-45})]:
            actual,reason=correct_action(self.proposal(name,args),holding=True,depth=None,history=history)
            self.assertEqual((actual.name,actual.args),('move',{'distance':.15}))
            self.assertEqual(reason,'reposition_after_full_zone_scan')
        for kwargs in ({'clear':False}, {'allow_exploration':False}):
            actual,_=correct_action(self.proposal('move',{'distance':.2},clear=kwargs.get('clear',True)),
                holding=True,depth=None,history=history,allow_exploration=kwargs.get('allow_exploration',True))
            self.assertEqual(actual.name,'turn')

    def test_delivery_scan_direction_is_consistent_and_translation_resets_sweep(self):
        history=[{'tool':'turn','args':{'degrees':45},'result':{'success':True}}] * 5
        actual,_=correct_action(self.proposal('turn',{'degrees':-45}),holding=True,depth=None,history=history)
        self.assertEqual(actual.args,{'degrees':45})
        history += [{'tool':'move','args':{'distance':.15},'result':{'success':True}}]
        actual,_=correct_action(self.proposal('move',{'distance':.2}),holding=True,depth=None,history=history)
        self.assertEqual(actual.name,'turn')

    def test_approach_rounding_never_exceeds_range_guard(self):
        actual,_=correct_action(self.proposal('pickup',{},[280,300,360,380]),
                               holding=False,depth=.14785,disabled=['pickup'])
        self.assertEqual(actual.name,'move')
        self.assertLessEqual(actual.args['distance'],.14785-.04)

    def test_collision_pickup_is_not_converted_back_to_blocked_approach(self):
        collision={'tool':'move','args':{'distance':.1102},'result':{'success':False,'collided':True}}
        actual,reason=correct_action(self.proposal('pickup',{},[210,280,390,440]),
            holding=False,depth=.196569,history=[collision],recovering=True)
        self.assertEqual(actual.name,'pickup')
        self.assertIsNone(reason)

    def test_collision_escape_takes_priority_over_visual_approach(self):
        for sign in (-1,1):
            collision={'tool':'move','args':{'distance':sign*.2},'result':{'success':False,'collided':True}}
            actual,reason=correct_action(self.proposal('move',{'distance':sign*.2},[210,280,390,440]),
                holding=False,depth=.196569,history=[collision],recovering=True)
            self.assertEqual(actual.args,{'distance':-sign*.15})
            self.assertEqual(reason,'collision_recovery_before_approach')

    def test_near_range_overrides_inaccurate_unclipped_box_with_pickup_trial(self):
        actual,_=correct_action(self.proposal('pickup',{},[210,280,390,440]),holding=False,depth=.150245)
        self.assertEqual(actual.name,'pickup')
        actual,_=correct_action(self.proposal('move',{'distance':.11},[210,280,390,440]),
            holding=False,depth=.15,disabled=['pickup'])
        self.assertLessEqual(actual.args['distance'],.03)

    def test_disabled_pickup_cannot_be_reintroduced(self):
        actual,_=correct_action(self.proposal('move',{'distance':.01},[220,300,420,480]),
                               holding=False,depth=.06,disabled=['pickup'])
        self.assertEqual(actual.name,'move')

    def test_no_forward_delivery_without_visible_floor_zone(self):
        for box in (None,[0,160,640,250]):
            actual,_=correct_action(self.proposal('move',{'distance':.2},box),holding=True,depth=None)
            self.assertEqual(actual.name,'turn')

    def test_delivery_aligns_to_visible_zone_on_right(self):
        actual,_=correct_action(self.proposal('move',{'distance':.2},[440,280,600,420],True),
                               holding=True,depth=None)
        self.assertEqual(actual.name,'turn')
        self.assertGreater(actual.args['degrees'],0)

    def test_blocked_path_prevents_forward_dispatch(self):
        actual,_=correct_action(self.proposal('move',{'distance':.2},[220,300,420,480],clear=False),
                               holding=False,depth=1)
        self.assertEqual(actual.name,'turn')

    def test_view_validation_strips_fields_and_rejects_invalid_bounds(self):
        view={'box':[0,100,200,480], 'path_clear':True, 'floor_zone':False}
        args, got=extract_target_view({'distance':.1,'target_view':view})
        self.assertEqual(args,{'distance':.1}); self.assertEqual(got,view)
        for box in ([0,0,641,480],[100,0,20,40],[0,0,20,float('nan')],[True,0,20,40]):
            with self.assertRaises(ValueError):
                extract_target_view({'target_view':{**view,'box':box}})
