"""Offline coverage of the exact live UI worker, with provider transports blocked."""
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rover_lab.live import HAIKU_MODEL, LiveRun


class LiveRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.live = LiveRun('http://127.0.0.1:1', self.temp.name)
        self.environment = patch.dict(os.environ, {'ROVER_LIVE_ENABLED': '1', 'ANTHROPIC_API_KEY': 'test-only',
                                                  'CLAUDE_MODEL': 'leave-this-unchanged'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(patch.stopall)
        patch('agent.claude.request', side_effect=AssertionError('Paid calls forbidden')).start()
        patch('agent.openai_client.request', side_effect=AssertionError('Paid calls forbidden')).start()
        self.engine = patch('rover_lab.live.EngineClient').start()
        self.engine.return_value.state.return_value = {'state': 'Idle'}
        self.live.operator = Mock(return_value={'state': 'Done', 'outcome': 'pass',
                                              'pov_manifest': 'private-path', 'pov_status': 'complete'})

    def test_disabled_missing_key_and_bad_caps_never_start_engine_or_provider(self):
        for cap in (0, 61, 2.5, True, '25', None):
            with self.assertRaises(ValueError):
                self.live.start('Fetch red sphere', cap)
        with patch.dict(os.environ, {'ROVER_LIVE_ENABLED': '0'}):
            with self.assertRaises(ValueError):
                self.live.start('Fetch red sphere', 25)
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}):
            with self.assertRaises(ValueError):
                self.live.start('Fetch red sphere', 25)
        self.engine.assert_not_called()
        self.live.operator.assert_not_called()

    def test_worker_pins_haiku_audits_and_finishes_without_leaking_private_fields(self):
        def fake_run(command, **kwargs):
            self.assertEqual(kwargs['claude'].model, HAIKU_MODEL)
            self.assertEqual(kwargs['max_decisions'], 25)
            kwargs['claude']._audit_request({'model': HAIKU_MODEL})
            folder = Path(kwargs['output_root']) / 'run-offline'
            folder.mkdir()
            return SimpleNamespace(status='engine_done', decisions=11, usage={'input_tokens': 8}, run_dir=folder)
        with patch('rover_lab.live.run_command', side_effect=fake_run):
            self.live.start('Fetch the red sphere and drop it in the green zone', 25)
            self.live.thread.join(3)
        self.assertFalse(self.live.running)
        start = self.live.operator.call_args_list[0].args
        self.assertEqual(start, ('/run/start', {'command': 'Fetch the red sphere and drop it in the green zone',
                                             'record_pov': True, 'expected_fixture_key': 'item_1'}))
        self.assertEqual(self.live.operator.call_args_list[-1].args, ('/run/finish', {'reason': 'abandoned'}))
        status = self.live.status()
        self.assertEqual(status['result']['outcome'], 'pass')
        self.assertNotIn('private-path', json.dumps(status))
        self.assertNotIn('test-only', json.dumps(status))
        self.assertEqual(os.environ['CLAUDE_MODEL'], 'leave-this-unchanged')
        self.assertTrue((self.live.attempt / 'claude_requests.jsonl').is_file())

    def test_conflicts_and_failures_cannot_reset_or_repeat_start(self):
        with self.live.lock:
            self.live.running = True
        with self.assertRaises(RuntimeError):
            self.live.start('Fetch red sphere', 25)
        with self.assertRaises(RuntimeError):
            self.live.reset()
        self.live.running = False
        with patch('rover_lab.live.run_command', side_effect=RuntimeError('private simulator error')):
            self.live.start('Fetch blue cylinder', 5)
            self.live.thread.join(3)
        self.assertEqual(self.live.status()['result']['status'], 'operator_error')
        self.assertNotIn('private simulator error', json.dumps(self.live.status()))
        self.assertEqual(self.live.operator.call_args_list[-1].args[0], '/run/finish')

    def test_start_failure_does_not_finish_someone_elses_run(self):
        self.live.operator.side_effect = RuntimeError('conflict')
        self.live.start('Fetch red sphere', 25)
        self.live.thread.join(3)
        self.assertEqual(self.live.operator.call_count, 1)
        self.assertFalse(self.live.status()['result']['finish_confirmed'])


if __name__ == '__main__':
    unittest.main()
