/* Explicit local operator controls. No API credentials enter the browser. */
(() => {
  const byId = id => document.getElementById(id);
  const token = document.querySelector('meta[name="jury-token"]').content;
  const mode = byId('run-mode'), command = byId('command');
  const fixedCommand = 'Fetch the red sphere and drop it in the green zone';
  let busy = false, state = null, active = false, liveSnapshot = null;
  let draft = fixedCommand;
  async function request(path, data) {
    const response = await fetch(path, {
      method: data === undefined ? 'GET' : 'POST', cache: 'no-store',
      headers: {'X-Jury-Token': token, ...(data === undefined ? {} : {'Content-Type':'application/json'})},
      body: data === undefined ? undefined : JSON.stringify(data), signal: AbortSignal.timeout(8000),
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || 'The rover request failed.');
    return result;
  }
  function render() {
    const live = mode.value === 'live';
    const locked = busy || active;
    byId('live-options').hidden = !live;
    mode.disabled = locked;
    command.disabled = locked || !live;
    for (const id of ['provider','decision-cap','expected-target','confirm-paid']) byId(id).disabled = locked || !live;
    byId('start').disabled = locked || !state || !['Idle','Done'].includes(state.state) ||
      (live && !byId('confirm-paid').checked);
    byId('start').textContent = live ? 'Start live agent' : 'Start scripted fetch';
    byId('reset').disabled = locked || !state || !['Idle','Done','Locate'].includes(state.state);
    byId('mode').textContent = live ? 'Claude/OpenAI navigation + local approach control · paid model calls' :
      'Scripted rehearsal · fixed red-sphere route · zero model calls';
    byId('status').textContent = state ? `${state.state} · Holding: ${state.holding ? 'yes' : 'no'} · Retries: ${state.retry_count}` : 'Engine unavailable';
    if (live && liveSnapshot) {
      const r = liveSnapshot.result;
      const parts = [`Total decisions: ${liveSnapshot.decisions}`, `Model calls: ${liveSnapshot.model_requests ?? 0}`,
        `Local control steps: ${liveSnapshot.local_control_steps ?? 0}`, `Collisions: ${liveSnapshot.collisions}`];
      if (r) {
        parts.push(`Result: ${r.verdict || 'unknown'}`, `Stop: ${r.agent_status}`);
        if (r.usage) parts.push(`Tokens in/out: ${r.usage.input_tokens ?? 0}/${r.usage.output_tokens ?? 0}`);
        if (r.error) parts.push(r.error);
      }
      if (liveSnapshot.run_id) parts.push(`Run: ${liveSnapshot.run_id}`);
      byId('live-result').textContent = parts.join(' · ');
    } else byId('live-result').textContent = '';
  }
  async function poll() {
    try {
      // The live status endpoint also reads real engine state, including offline runs.
      liveSnapshot = await request('/scenario-1/live/status');
      state = liveSnapshot.state;
      const offline = await request('/scenario-1/api/offline-demo');
      active = liveSnapshot.running || offline.status === 'running' ||
        (state && !['Idle','Done'].includes(state.state));
      render();
    } catch (error) {
      byId('feedback').textContent = error.message;
      state = null;
      render();
    } finally { setTimeout(poll, document.hidden ? 2000 : 750); }
  }
  mode.addEventListener('change', () => {
    if (mode.value === 'live') command.value = draft;
    else { draft = command.value; command.value = fixedCommand; }
    byId('confirm-paid').checked = false;
    byId('feedback').textContent = '';
    render();
  });
  byId('confirm-paid').addEventListener('change', render);
  byId('controls').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || active) return;
    const live = mode.value === 'live';
    busy = true; render();
    try {
      if (live) {
        const cap = Number(byId('decision-cap').value);
        if (!Number.isInteger(cap) || cap < 1 || cap > 60) throw Error('Choose a decision cap from 1 to 60.');
        if (!command.value.trim()) throw Error('Enter a fetch command.');
        await request('/scenario-1/live/start', {command:command.value.trim(), provider:byId('provider').value,
          max_decisions:cap, expected_fixture_key:byId('expected-target').value || null,
          confirm_paid:byId('confirm-paid').checked});
      } else await request('/scenario-1/api/run/start', {command:fixedCommand});
      active = true;
      byId('feedback').textContent = live ? 'Model navigation started with local approach control. All actions and decision sources are being logged.' : 'Scripted fetch started.';
    } catch (error) { byId('feedback').textContent = error.message; }
    finally { busy = false; render(); }
  });
  byId('reset').addEventListener('click', async () => {
    if (busy || active) return;
    busy = true; render();
    try {
      await request(mode.value === 'live' ? '/scenario-1/live/reset' : '/scenario-1/api/run/reset', {});
      byId('feedback').textContent = 'Layout reset. You can edit your next live command.';
      byId('confirm-paid').checked = false;
    } catch (error) { byId('feedback').textContent = error.message; }
    finally { busy = false; render(); }
  });
  startFeed('camera', '/scenario-1/api/preview?jury=' + encodeURIComponent(token));
  poll();
})();
