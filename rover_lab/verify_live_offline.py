"""Exercise the new UI's live HTTP path with real Webots and a mocked Claude API.

Fixture waypoints exist only in this offline verifier. They never enter the agent
context. This verifies plumbing and recording, not autonomous model navigation.
"""
import argparse
import base64
import io
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image, ImageChops

from rover_lab import dashboard
from rover_lab.live import HAIKU_MODEL


def verify(engine_port, ui_port, run_dir):
    output = Path(run_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    layout = output / 'test-layout.txt'
    layout.write_text('A')
    actions = iter([
        # Deliberately fail an early pickup, then require a corrected pose before
        # offering it again. Exercises the live failure that consumed 15 calls.
        ('pickup', None),
        ('face', (-0.96, 0.46)), ('drive', (-0.96, 0.46)),
        ('face', (-0.55, 0.65)), ('pickup', None),
        ('face', (-0.65, 0.46)), ('drive', (-0.65, 0.46)),
        ('face', (-0.65, -1.65)), ('drive', (-0.65, -1.65)),
        ('face', (1.28, -1.65)), ('drive', (1.28, -1.65)), ('drop', None),
    ])
    requests = []

    def mock_api(url, timeout, *, payload, headers):
        assert url == 'https://api.anthropic.com/v1/messages'
        assert payload['model'] == HAIKU_MODEL
        assert 'expected_fixture_key' not in json.dumps(payload)
        requests.append(payload)
        offered = {tool['name'] for tool in payload['tools']}
        if len(requests) == 2:
            assert 'pickup' not in offered, 'Failed pickup was still offered at the same pose'
        context = json.loads(payload['messages'][0]['content'][0]['text'])
        state = context['state']
        action, target = next(actions)
        if action == 'face':
            name = 'turn'
            args = {'degrees': math.remainder(-math.degrees(math.atan2(target[1]-state['y'], target[0]-state['x'])) - state['heading'], 360)}
        elif action == 'drive':
            name, args = 'move', {'distance': math.hypot(target[0]-state['x'], target[1]-state['y'])}
            args['distance'] = min(args['distance'], context.get('max_forward_distance', args['distance']))
        else:
            name, args = action, {}
        return 'application/json', json.dumps({'stop_reason': 'tool_use',
            'content': [{'type': 'tool_use', 'name': name, 'input': args}],
            'usage': {'input_tokens': 0, 'output_tokens': 0}}).encode()

    env = {'ROVER_DEMO_LAYOUT': str(layout), 'ROVER_LIVE_ENABLED': '1',
           'ANTHROPIC_API_KEY': 'offline-fake-key',
           'ROVER_LAB_SCENARIO_ONE_ENGINE': f'http://127.0.0.1:{engine_port}'}
    with patch.dict(os.environ, env), patch.object(dashboard, 'RUN_DIR', output), \
         patch.object(dashboard, 'OUTPUT', output / 'evidence'), \
         patch.object(dashboard, 'SCENARIO_ONE_ORIGIN', f'http://127.0.0.1:{ui_port}'), \
         patch('agent.claude.request', side_effect=mock_api), \
         patch('agent.openai_client.request', side_effect=AssertionError('Provider calls forbidden')):
        server = dashboard.create_server(0, engine_port)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        with urlopen(base + '/scenario-1') as response:
            page = response.read().decode()
        assert 'run-mode' in page and 'decision-cap' in page
        token = re.search(r'name="jury-token" content="([^"]+)"', page)[1]

        def call(path, data=None, *, auth=True, origin=True):
            headers = {'Content-Type': 'application/json'}
            if auth:
                headers['X-Jury-Token'] = token
            if origin:
                headers['Origin'] = base
            request = Request(base + '/scenario-1/api' + path,
                              data=None if data is None else json.dumps(data).encode(), headers=headers)
            with urlopen(request, timeout=40) as response:
                return json.load(response)

        def rejected(path, data, code, **kwargs):
            try:
                call(path, data, **kwargs)
            except HTTPError as error:
                with error:
                    assert error.code == code, error.code
            else:
                raise AssertionError('Request unexpectedly accepted')

        try:
            call('/live/reset', {})
            rejected('/live/start', {'command': 'Fetch red sphere', 'max_decisions': 25}, 403, auth=False)
            rejected('/live/start', {'command': 'Fetch red sphere', 'max_decisions': 25}, 403, origin=False)
            rejected('/live/start', {'command': 'Fetch red sphere', 'max_decisions': 2.5}, 400)
            assert not requests
            command = 'Fetch the red sphere and drop it in the green zone'
            call('/live/start', {'command': command, 'max_decisions': 25})
            rejected('/live/start', {'command': command, 'max_decisions': 25}, 409)
            rejected('/live/reset', {}, 409)
            rejected('/run/reset', {}, 409)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                status = call('/live/status')
                if not status['running']:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError('Offline live-path verification timed out')
            assert status['result']['outcome'] == 'pass', status
            assert status['result']['decisions'] == 12, status
            assert 'pov_manifest' not in json.dumps(status)
            live = server.live_run
            record = json.loads((live.attempt / 'operator-result.json').read_text())
            record['execution_mode'] = 'offline_scripted_transport_stub'
            (live.attempt / 'operator-result.json').write_text(json.dumps(record, indent=2))
            assert record['verdict']['pov_status'] == 'complete', record
            journal = next(live.attempt.glob('run-*/steps.jsonl'))
            rows = [json.loads(line) for line in journal.read_text().splitlines()]
            assert not any(row['result'].get('collided') for row in rows), 'Contact occurred during delivery'
            assert rows[0]['tool'] == 'pickup' and rows[0]['result'] == {'success': False}
            for req, row in zip(requests, rows, strict=True):
                raw = req['messages'][0]['content'][1]['source']['data']
                image = Image.open(io.BytesIO(base64.b64decode(raw))).convert('RGB')
                frame = Image.open(journal.parent / row['frame_path']).convert('RGB')
                assert image.size == (640, 480)
                assert ImageChops.difference(image, frame).getbbox() is None
            report = {'execution_mode': 'offline_scripted_transport_stub', 'provider_calls': 0,
                      'collisions': 0,
                      'requests_matched_to_frames': len(requests), 'verdict': 'pass',
                      'new_ui_routes': 'passed', 'authentication_and_conflict_checks': 'passed',
                      'capture': record['verdict']['pov_status']}
            (output / 'offline-live-ui-report.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
            call('/live/reset', {})
        finally:
            server.live_run.thread.join(40) if server.live_run.thread else None
            server.shutdown()
            server.server_close()
            worker.join(3)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine-port', type=int, required=True)
    parser.add_argument('--ui-port', type=int, required=True)
    parser.add_argument('--run-dir', required=True)
    args = parser.parse_args()
    verify(args.engine_port, args.ui_port, args.run_dir)
