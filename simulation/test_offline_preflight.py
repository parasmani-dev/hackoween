"""Offline fixture admission must not confuse terminal completion with success."""
import contextlib
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from simulation import check_http_fetch as launcher
from simulation.offline_agent_check import check_agent


class OfflinePreflightTests(unittest.TestCase):
    def test_fixture_disables_inherited_dashboard_driver(self):
        with patch.dict(os.environ, {'ROVER_OFFLINE_DEMO':'1'}):
            env = launcher.fixture_ui_environment(18886)
            self.assertEqual(env['ROVER_OFFLINE_DEMO'], '0')
            self.assertEqual(env['ROVER_ENGINE_PORT'], '18886')
            self.assertEqual(os.environ['ROVER_OFFLINE_DEMO'], '1')

    def test_scripted_check_rejects_scored_failure_or_missing_verdict(self):
        for outcome in ('fail_wrong_object', 'fail_not_reached', 'fail_collision', None):
            with self.subTest(outcome=outcome), patch.object(launcher, 'run_live', return_value={
                'verdict': {'state':'Done', 'outcome':outcome}, 'pov_status':'complete'
            }), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(RuntimeError, 'private pass verdict'):
                    check_agent(Mock(), Mock())
                self.assertNotIn('PASS:', output.getvalue())

    def test_scripted_check_accepts_private_pass(self):
        with patch.object(launcher, 'run_live', return_value={'verdict':{'outcome':'pass'}}), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            check_agent(Mock(), Mock())
        self.assertIn('PASS:', output.getvalue())

    def test_launcher_still_preserves_scored_failure(self):
        with TemporaryDirectory() as directory:
            verdict = {'state':'Done', 'outcome':'fail_wrong_object', 'pov_status':'complete'}
            args = SimpleNamespace(execution_mode='offline_scripted', command='fixture',
                expected_fixture_key='item_1', record_pov=True, provider='claude',
                max_decisions=20, agent_root=Path.cwd())
            with patch.object(launcher, 'mkdtemp', return_value=directory), \
                 patch.object(launcher, 'prepare_attempt', return_value={}), \
                 patch.object(launcher, 'urlopen', return_value=io.BytesIO(b'{"state":"Locate"}')), \
                 patch.object(launcher, 'finish_run', return_value=verdict), \
                 patch.object(launcher.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"status":"engine_done"}')), \
                 contextlib.redirect_stdout(io.StringIO()):
                record = launcher.run_live(SimpleNamespace(server_port=1), SimpleNamespace(look=lambda:b'jpeg'), args)
            self.assertEqual(record['verdict']['outcome'], 'fail_wrong_object')
            saved = json.loads((Path(directory)/'operator-result.json').read_text())
            self.assertEqual(saved['pov_status'], 'complete')
            self.assertEqual(saved['verdict']['outcome'], 'fail_wrong_object')
