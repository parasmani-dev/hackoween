"""Offline coverage of the exact live UI worker, with provider transports blocked."""
import json
import os
import unittest
import subprocess
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

    def test_local_run_needs_no_key_and_never_constructs_provider(self):
        def fake_run(command, **kwargs):
            self.assertEqual(kwargs['provider'], 'local')
            self.assertTrue(kwargs['camera_control'])
            self.assertEqual(command, 'Fetch the red sphere and drop it in the green zone')
            folder=Path(kwargs['output_root'])/'run-local';folder.mkdir()
            return SimpleNamespace(status='engine_done',decisions=33,usage={'input_tokens':0,'output_tokens':0},run_dir=folder)
        with patch.dict(os.environ, {'ROVER_LIVE_ENABLED':'0','ANTHROPIC_API_KEY':'','OPENAI_API_KEY':''}), \
             patch('agent.claude.ClaudeClient', side_effect=AssertionError('Provider constructed')), \
             patch('rover_lab.live.run_command',side_effect=fake_run):
            self.live.start('bring the red ball to the green zone',50,'local')
            self.live.thread.join(3)
        status=self.live.status()
        self.assertFalse(status['running'])
        self.assertEqual(status['execution_mode'],'local')
        self.assertEqual(status['result']['provider_calls'],0)
        self.assertIsNone(status['model'])
        self.assertFalse((self.live.attempt/'claude_requests.jsonl').exists())

    def test_local_parse_error_and_unknown_mode_do_not_start_engine(self):
        with self.assertRaises(ValueError):self.live.start('move forward 1 m then dance',50,'local')
        with self.assertRaises(ValueError):self.live.start('drop',50,'other')
        self.engine.assert_not_called();self.live.operator.assert_not_called()

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
            self.assertEqual(self.live.status()['phase'], 'deciding')
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

    def test_reset_clears_old_actions_but_preserves_saved_evidence(self):
        self.live.attempt = Path(self.temp.name) / 'attempt-test'
        folder = self.live.attempt / 'run-test'
        folder.mkdir(parents=True)
        journal = folder / 'steps.jsonl'
        journal.write_text(json.dumps({'step': 1, 'tool': 'pickup', 'args': {}, 'result': {'success': False}}) + '\n')
        self.live.command = 'old command'
        self.live.max_decisions = 25
        self.assertEqual(self.live.status()['decisions'], 1)
        self.live.reset()
        status = self.live.status()
        self.assertEqual(status['actions'], [])
        self.assertEqual(status['decisions'], 0)
        self.assertIsNone(status['command'])
        self.assertIsNone(status['max_decisions'])
        self.assertEqual(status['phase'], 'idle')
        self.assertTrue(journal.is_file())

    def test_finish_failure_is_explicit_and_does_not_expose_error_details(self):
        folder = Path(self.temp.name) / 'run-test'
        folder.mkdir()
        fake = SimpleNamespace(status='decision_limit', decisions=5, usage={}, run_dir=folder)
        with patch('rover_lab.live.run_command', return_value=fake), \
             patch('rover_lab.live.finish_run', side_effect=RuntimeError('secret simulator path')):
            self.live.start('Fetch red sphere', 5)
            self.live.thread.join(3)
        status = self.live.status()
        self.assertFalse(status['running'])
        self.assertEqual(status['phase'], 'failed')
        self.assertEqual(status['result']['outcome'], 'unconfirmed')
        self.assertFalse(status['result']['finish_confirmed'])
        self.assertEqual(status['result']['capture_status'], 'unconfirmed')
        self.assertNotIn('secret simulator path', json.dumps(status))

    def test_artifact_failure_releases_run_and_is_distinct_from_delivery_success(self):
        folder = Path(self.temp.name) / 'run-test'
        folder.mkdir()
        fake = SimpleNamespace(status='engine_done', decisions=5, usage={}, run_dir=folder)
        with patch('rover_lab.live.run_command', return_value=fake), \
             patch('pathlib.Path.write_text', side_effect=OSError('private disk path')):
            self.live.start('Fetch red sphere', 5)
            self.live.thread.join(3)
        status = self.live.status()
        self.assertFalse(status['running'])
        self.assertEqual(status['result']['status'], 'artifact_error')
        self.assertEqual(status['result']['outcome'], 'pass')
        self.assertTrue(status['result']['finish_confirmed'])
        self.assertNotIn('private disk path', json.dumps(status))

    def test_ui_progress_reload_reset_and_stale_poll_do_not_trigger_api_calls(self):
        # Run the real page script against asynchronous HTTP responses and a small DOM.
        # This exercises UI lifecycle behavior without a browser or any provider network.
        script = Path(__file__).parent / 'site' / 'operator.js'
        harness = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor() {this.value = ''; this.disabled = false; this.textContent = ''; this.handlers = {};}
  addEventListener(name, callback) { this.handlers[name] = callback; }
  querySelector() { return this.option ??= new Element(); }
  replaceChildren(...children) { this.children = children; }
}
const elements = Object.fromEntries(['run-mode','command','decision-cap','start','reset','mode','status',
  'feedback','controls','action-log','camera','camera-note','run-summary'].map(id => [id, new Element()]));
elements['run-mode'].value = 'offline'; elements['decision-cap'].value = '25';
let data = {
  '/state': {state:'Locate', holding:false, retry_count:0},
  '/offline-demo': {status:'idle',command:'Fetch red sphere'},
  '/live/status': {enabled:true,ready:true,running:true,phase:'deciding',decisions:7,max_decisions:25,
    command:'Fetch custom target',actions:[],result:null}
};
const calls=[];
const context=vm.createContext({document:{hidden:false,querySelector:()=>({content:'token'}),
  getElementById:id=>elements[id],createElement:()=>new Element()},
  fetch: async (path, options) => {calls.push({path,options}); return {ok:true,json:async()=>data[path.replace('/scenario-1/api','')]};},
  AbortSignal, setTimeout:()=>{}, startFeed:()=>{}, console});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await flush();
  assert.match(elements.feedback.textContent,/Haiku run in progress.*7 \/ 25/);
  assert.equal(elements.command.value,'Fetch custom target');
  assert.equal(elements.start.disabled,true);
  assert.equal(calls.filter(c=>c.options.method==='POST').length,0);
  data['/state']={state:'Done',holding:false,retry_count:0};
  data['/live/status']={...data['/live/status'],running:false,phase:'completed',result:{
    outcome:'fail_not_reached',status:'decision_limit',decisions:25,finish_confirmed:true,capture_status:'complete'}};
  vm.runInContext("runMode=null; mode.value='offline'; refresh()",context);
  await flush();
  assert.equal(elements['run-mode'].value,'live');
  assert.match(elements.feedback.textContent,/fail_not_reached.*25 decisions/);
  data['/live/status'].result.status='navigation_stalled';
  await vm.runInContext('refresh()',context);
  assert.match(elements['run-summary'].textContent,/Run stopped: navigation/);
  assert.equal(elements.reset.disabled,false);
  assert.equal(calls.filter(c=>c.options.method==='POST').length,0);
  // A response to a poll begun before a lifecycle action must not overwrite that action's UI.
  vm.runInContext("refresh(); revision += 1; byId('feedback').textContent='Newer action';",context);
  await flush();
  assert.equal(elements.feedback.textContent,'Newer action');
  // Switching back to offline is explicit and cannot launch a provider.
  elements['run-mode'].value='offline'; elements['run-mode'].handlers.change();
  assert.equal(elements.feedback.textContent,'');
  assert.equal(calls.filter(c=>c.options.method==='POST').length,0);
  data['/state']={state:'Idle',holding:false,retry_count:0};
  data['/live/status']={...data['/live/status'],enabled:false,ready:false,result:null,running:false};
  elements['run-mode'].value='local'; elements.command.value='move forward 50 cm';
  elements['decision-cap'].value='50';
  await vm.runInContext('refresh()',context);
  elements['run-mode'].handlers.change();
  assert.equal(elements.command.disabled,false);
  assert.equal(elements.command.value,'move forward 50 cm');
  assert.equal(elements.start.disabled,false);
  await elements.controls.handlers.submit({preventDefault(){}});
  const sent=calls.filter(c=>c.options.method==='POST').at(-1);
  assert.equal(sent.path,'/scenario-1/api/live/start');
  assert.equal(JSON.parse(sent.options.body).execution_mode,'local');
  assert.equal(JSON.parse(sent.options.body).command,'move forward 50 cm');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        completed = subprocess.run(['node', '-e', harness, str(script)], capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
