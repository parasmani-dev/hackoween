"""Concurrent old-tab reset cannot slip past a live-run reservation."""
import io
import json
import re
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from rover_lab import dashboard


class LifecycleTests(unittest.TestCase):
    def test_offline_reset_cannot_race_live_start(self):
        entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
        running = False
        forwarded = []

        def reserve(command, cap, execution_mode="live"):
            nonlocal running
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test reservation timed out')
            running = True

        def upstream(request, **kwargs):
            path = request if isinstance(request, str) else request.full_url
            forwarded.append(path)
            return io.BytesIO(b'{"status":"idle"}')

        with patch.object(dashboard, 'Demo'), patch.object(dashboard, 'LiveRun') as live, \
             patch.object(dashboard, 'urlopen', side_effect=upstream):
            live.return_value.start.side_effect = reserve
            live.return_value.status.side_effect = lambda: {'running': running}
            server = dashboard.create_server(0, 1)
            inner_lock = server.scenario_one_lifecycle
            class ObservedLock:
                def __enter__(self):
                    if inner_lock.locked():
                        waiting.set()
                    inner_lock.acquire()
                def __exit__(self, *args):
                    inner_lock.release()
            server.scenario_one_lifecycle = ObservedLock()
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f'http://127.0.0.1:{server.server_port}'
            with urlopen(base+'/scenario-1') as response:
                token = re.search(r'name="jury-token" content="([^"]+)"', response.read().decode())[1]
            results = {}

            def post(path):
                try:
                    with urlopen(Request(base+'/scenario-1/api'+path, data=json.dumps(
                        {'command': 'Fetch red sphere', 'max_decisions': 60}).encode(), headers={
                            'Origin': base, 'X-Jury-Token': token, 'Content-Type': 'application/json'}), timeout=4) as response:
                        results[path] = response.status
                except HTTPError as error:
                    with error:
                        results[path] = error.code

            start = threading.Thread(target=post, args=('/live/start',))
            reset = threading.Thread(target=post, args=('/run/reset',))
            try:
                start.start()
                self.assertTrue(entered.wait(2))
                reset.start()
                self.assertTrue(waiting.wait(2), 'Reset did not wait on the lifecycle reservation')
                release.set()
                start.join(4)
                reset.join(4)
                self.assertEqual(results, {'/live/start': 202, '/run/reset': 409})
                self.assertFalse(any(path.endswith('/run/reset') for path in forwarded))
            finally:
                release.set()
                server.shutdown()
                server.server_close()
                worker.join(3)
