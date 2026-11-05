const token = document.querySelector('meta[name="jury-token"]').content;
const prefix = '/scenario-1/api';
const byId = id => document.getElementById(id);
const mode = byId('run-mode');
const command = byId('command');
const cap = byId('decision-cap');
const start = byId('start');
const reset = byId('reset');
let snapshot = null;
let pending = false;
let runMode = null;

async function api(path, data) {
  const response = await fetch(prefix + path, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {'X-Jury-Token': token, ...(data === undefined ? {} : {'Content-Type': 'application/json'})},
    body: data === undefined ? undefined : JSON.stringify(data),
    cache: 'no-store', signal: AbortSignal.timeout(12000),
  });
  const value = await response.json();
  if (!response.ok) throw Error(value.error || 'Rover request failed. Check status before retrying.');
  return value;
}

function render() {
  const live = mode.value === 'live';
  const running = pending || snapshot?.live.running || snapshot?.offline.status === 'running';
  const idle = snapshot?.state.state === 'Idle';
  mode.disabled = !snapshot || running || !idle;
  mode.querySelector('[value="live"]').disabled = !snapshot?.live.enabled || !snapshot?.live.ready;
  command.disabled = !live || running || !idle;
  cap.disabled = !live || running || !idle;
  start.disabled = !snapshot || running || !idle || (live && (!snapshot.live.enabled || !snapshot.live.ready));
  reset.disabled = !snapshot || running || idle;
  if (!live && snapshot) command.value = snapshot.offline.command;
  start.textContent = live ? 'Start Haiku fetch' : 'Start offline rehearsal';
  byId('mode').textContent = live
    ? 'Claude Haiku 4.5 · Start makes paid API calls · decision cap applies'
    : 'Offline scripted rehearsal · zero model calls';
  if (snapshot) {
    const state = snapshot.state;
    byId('status').textContent = `${state.state} · Holding: ${state.holding ? 'yes' : 'no'} · Retries: ${state.retry_count}`;
    const result = snapshot.live.result;
    byId('action-log').replaceChildren(...(live ? snapshot.live.actions : []).map(action => {
      const item = document.createElement('li');
      item.textContent = `${action.step}: ${action.tool ?? 'Provider error'} ${JSON.stringify(action.args)} · ${JSON.stringify(action.result)}`;
      return item;
    }));
    if (runMode === 'live' && result && !snapshot.live.running) {
      byId('feedback').textContent = `${result.outcome} · ${result.status} · ${result.decisions ?? 0} decisions`;
    } else if (runMode === 'offline') {
      byId('feedback').textContent = {running: 'Offline rehearsal running.', pass: 'PASS: Red sphere delivered.',
        fail: 'Offline rehearsal failed. Check the local logs.', idle: ''}[snapshot.offline.status] || '';
    }
  }
}

mode.addEventListener('change', render);
byId('controls').addEventListener('submit', async event => {
  event.preventDefault();
  if (start.disabled) return;
  const selectedMode = mode.value;
  const decisions = Number(cap.value);
  if (selectedMode === 'live' && (!Number.isInteger(decisions) || decisions < 1 || decisions > 60)) {
    byId('feedback').textContent = 'Choose a whole-number decision cap from 1 to 60.';
    return;
  }
  pending = true;
  render();
  try {
    await api(selectedMode === 'live' ? '/live/start' : '/run/start', selectedMode === 'live'
      ? {command: command.value.trim(), max_decisions: decisions} : {command: command.value.trim()});
    runMode = selectedMode;
    byId('feedback').textContent = selectedMode === 'live' ? 'Haiku run starting…' : 'Offline rehearsal starting…';
  } catch (error) { byId('feedback').textContent = error.message; }
  finally { pending = false; }
});
reset.addEventListener('click', async () => {
  if (reset.disabled) return;
  pending = true;
  render();
  try {
    await api(runMode === 'offline' ? '/run/reset' : '/live/reset', {});
    runMode = null;
    byId('feedback').textContent = 'Rover reset.';
  } catch (error) { byId('feedback').textContent = error.message; }
  finally { pending = false; }
});

async function refresh() {
  try {
    const [state, offline, live] = await Promise.all([api('/state'), api('/offline-demo'), api('/live/status')]);
    if (!pending) {
      snapshot = {state, offline, live};
      if (live.running) { mode.value = 'live'; runMode = 'live'; }
      if (offline.status === 'running') { mode.value = 'offline'; runMode = 'offline'; }
      render();
    }
  } catch {
    snapshot = null;
    render();
    byId('status').textContent = 'Waiting for the rover engine.';
  } finally { setTimeout(refresh, document.hidden ? 1500 : 500); }
}
byId('camera').hidden = false;
byId('camera-note').id = 'camera-state';
startFeed('camera', prefix + '/preview?jury=' + encodeURIComponent(token));
render();
refresh();
