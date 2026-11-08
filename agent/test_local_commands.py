import json
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
from PIL import Image
from agent.local_commands import parse_command,LocalPolicy,LocalClarification
from agent.runner import run_command


class LocalCommandTests(unittest.TestCase):
    def test_fetch_aliases_normalize_without_fixture_coordinates(self):
        for text in ('bring the red ball to the green zone','GET a red sphere into a green area',
                     'retrieve red sphere and place it in green drop zone',
                     'pick up the red sphere then deliver it to the green zone'):
            plan=parse_command(text)
            self.assertEqual(plan.kind,'fetch')
            self.assertEqual(plan.canonical,'Fetch the red sphere and drop it in the green zone')

    def test_incomplete_request_asks_for_destination_and_ambiguous_object_asks_shape(self):
        for text,question in [('fetch the red sphere','Which destination zone'),
                              ('bring the blue object','Which shape')]:
            with self.subTest(text=text),self.assertRaisesRegex(LocalClarification,question):
                parse_command(text)

    def test_movement_sequences_expand_bounded_steps_and_preserve_signs(self):
        plan=parse_command('move forward 1 metre, turn left 90 degrees, go backward 25 cm; pick up then drop')
        self.assertEqual(len(plan.actions),9)
        self.assertEqual(plan.actions[0],('move',{'distance':.25}))
        self.assertEqual(plan.actions[4],('turn',{'degrees':-45.0}))
        self.assertEqual(plan.actions[6],('move',{'distance':-.25}))
        self.assertEqual(plan.actions[-2:],(('pickup',{}),('drop',{})))

    def test_unknown_ambiguous_and_over_budget_commands_are_rejected(self):
        cases=('', 'dance', 'fetch red sphere', 'move forward -1 m', 'move forward NaN m',
               'move forward 11 m', 'turn left 361 degrees', 'move forward 1',
               'move forward 0 m','drop; import os','move forward 1 m and dance',
               'fetch red sphere and drop it in green zone and dance',
               ',drop','drop,,pick up','move forward 10 m, move forward 10 m')
        for text in cases:
            with self.subTest(text=text),self.assertRaises(ValueError):parse_command(text)

    def engine(self):
        engine=Mock();engine.state.return_value={'state':'Locate','x':0,'y':0,'heading':0,'holding':False,'retry_count':0}
        stream=BytesIO();Image.new('RGB',(640,480),(190,190,180)).save(stream,format='JPEG')
        engine.look.return_value=stream.getvalue();engine.depth.return_value=None
        engine.execute.return_value={'success':True,'collided':False}
        return engine

    def test_local_sequence_finishes_without_provider_or_extra_decision(self):
        with TemporaryDirectory() as folder,patch('agent.claude.request',side_effect=AssertionError('API forbidden')),patch('agent.openai_client.request',side_effect=AssertionError('API forbidden')):
            plan=parse_command('move forward 50 cm then turn right 45 degrees')
            result=run_command(plan.canonical,engine=self.engine(),claude=LocalPolicy(plan),provider='local',output_root=folder)
            self.assertEqual((result.status,result.decisions),('local_complete',3))
            self.assertEqual(result.usage,{'input_tokens':0,'output_tokens':0})
            self.assertFalse(list(result.run_dir.glob('*requests*')))

    def test_local_sequence_stops_on_failed_or_incompatible_action(self):
        for text,success,status in [('move forward 25 cm, turn right 45 degrees',False,'local_action_failed'),('drop',True,'local_action_rejected')]:
            with TemporaryDirectory() as folder:
                engine=self.engine();engine.execute.return_value={'success':success,'collided':not success}
                plan=parse_command(text)
                result=run_command(plan.canonical,engine=engine,claude=LocalPolicy(plan),provider='local',output_root=folder)
                self.assertEqual(result.status,status)
                self.assertEqual(result.decisions,1)
                self.assertEqual(engine.execute.call_count,0 if text=='drop' else 1)
