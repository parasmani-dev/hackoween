const token = document.querySelector('meta[name="jury-token"]').content;
const prefix = '/scenario-1/api';
const byId = id => document.getElementById(id);
const mode = byId('run-mode');
const command = byId('command');
const cap = byId('decision-cap');
const start = byId('start');
const reset = byId('reset');
const runSummary = byId('run-summary');
let snapshot = null;
let pending = false;
let runMode = null;
let revision = 0;

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
  command.disabled = running || !idle;
  cap.disabled = running || !idle;
  start.disabled = !snapshot || running || !idle || (live && (!snapshot.live.enabled || !snapshot.live.ready));
  reset.disabled = !snapshot || running || idle;
  if (mode.value === 'offline' && snapshot) command.value = snapshot.offline.command;
  start.textContent = live ? 'Start Haiku fetch' : 'Start local run';
  byId('mode').textContent = live
    ? 'Claude Haiku 4.5 + camera controller · Start makes paid API calls · decision cap applies'
    : 'Local parser + camera navigation · no API key or model calls';
  if (snapshot) {
    const state = snapshot.state;
    byId('status').textContent = `${state.state} · Holding: ${state.holding ? 'yes' : 'no'} · Retries: ${state.retry_count}`;
    const result = snapshot.live.result;
    byId('action-log').replaceChildren(...(mode.value !== 'offline' ? snapshot.live.actions : []).map(action => {
      const item = document.createElement('li');
      item.textContent = `${action.step}: ${action.tool ?? 'Provider error'} ${JSON.stringify(action.args)} · ${JSON.stringify(action.result)}`;
      return item;
    }));
    if (snapshot.live.running) {
      const phase = {starting: 'Preparing the rover', deciding: snapshot.live.execution_mode === 'local' ? 'Local run in progress' : 'Haiku run in progress',
        finishing: 'Finalizing verdict and recording'}[snapshot.live.phase] || 'Haiku run in progress';
      byId('feedback').textContent = `${phase} · ${snapshot.live.decisions ?? 0} / ${snapshot.live.max_decisions ?? cap.value} decisions`;
    } else if ((runMode === 'live' || runMode === 'local') && result) {
      const notes = [];

      if (result.status === 'local_action_failed') notes.push('Local sequence stopped after a failed action. Check the camera and reset before retrying.');
      if (result.status === 'local_action_rejected') notes.push('Local sequence stopped because an action was unsafe or incompatible with the holding state.');
      if (result.status === 'navigation_stalled') notes.push('Run stopped: navigation could not make progress after repeated blocked proposals. It is not still running. Reset layout before a new run.');
      if (result.status === 'decision_limit') notes.push('Run stopped at the configured call limit. Reset layout before a new run.');
      if (result.finish_confirmed === false) notes.push('Finish unconfirmed; check the engine before resetting.');
      if (result.status === 'artifact_error') notes.push('Run evidence could not be saved.');
      if (result.capture_status && result.capture_status !== 'complete') notes.push('Recording is incomplete or unconfirmed.');
      const local = runMode === 'local';
      const outcome = local && result.status === 'local_complete' ? 'Local sequence completed' : result.outcome;
      byId('feedback').textContent = `${outcome} · ${result.decisions ?? 0} ${local ? 'actions · 0 API calls' : 'decisions'}${notes.length ? ' · ' + notes.join(' ') : ''}`;
    } else if (runMode === 'offline') {
      byId('feedback').textContent = {running: 'Offline rehearsal running.', pass: 'PASS: Red sphere delivered.',
        fail: 'Offline rehearsal failed. Check the local logs.', idle: ''}[snapshot.offline.status] || '';
    }
  }
  const interpreted = byId('interpreted-command');
  if (interpreted) interpreted.textContent = mode.value === 'local' && snapshot?.live.interpreted_command ? `Interpreted as: ${snapshot.live.interpreted_command}` : '';
  if (runSummary) runSummary.textContent = byId('feedback').textContent || (snapshot ? 'Ready. Start a run using the controls above.' : 'Connecting to run status...');
}

mode.addEventListener('change', () => {
  runMode = mode.value;
  byId('feedback').textContent = '';
  render();
});
byId('controls').addEventListener('submit', async event => {
  event.preventDefault();
  if (start.disabled) return;
  const selectedMode = mode.value;
  const decisions = Number(cap.value);
  if (!Number.isInteger(decisions) || decisions < 1 || decisions > 60) {
    byId('feedback').textContent = 'Choose a whole-number decision cap from 1 to 60.';
    return;
  }
  pending = true;
  revision += 1;
  render();
  try {
    await api(selectedMode !== 'offline' ? '/live/start' : '/run/start', selectedMode !== 'offline'
      ? {command: command.value.trim(), max_decisions: decisions, execution_mode: selectedMode} : {command: command.value.trim()});
    runMode = selectedMode;
    byId('feedback').textContent = selectedMode === 'live' ? 'Haiku run starting…' : 'Local command starting…';
  } catch (error) { byId('feedback').textContent = error.message; }
  finally { revision += 1; pending = false; }
});
reset.addEventListener('click', async () => {
  if (reset.disabled) return;
  pending = true;
  revision += 1;
  render();
  try {
    await api(runMode === 'offline' ? '/run/reset' : '/live/reset', {});
    runMode = null;
    byId('feedback').textContent = 'Rover reset.';
  } catch (error) { byId('feedback').textContent = error.message; }
  finally { revision += 1; pending = false; }
});

async function refresh() {
  const observedRevision = revision;
  try {
    const [state, offline, live] = await Promise.all([api('/state'), api('/offline-demo'), api('/live/status')]);
    if (!pending && observedRevision === revision) {
      snapshot = {state, offline, live};
      if (live.running || (runMode === null && live.result)) { mode.value = live.execution_mode || 'live'; runMode = mode.value; }
      if (live.running && live.command) command.value = live.command;
      if (live.running && live.max_decisions) cap.value = live.max_decisions;
      if (offline.status === 'running') { mode.value = 'offline'; runMode = 'offline'; }
      render();
    }
  } catch {
    if (pending || observedRevision !== revision) return;
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
