const byId = id => document.getElementById(id);
let running = false;
let replay;
let selected = 0;
let timer;
function text(id, value) {
  if (byId(id).textContent !== value) byId(id).textContent = value;
}
function pause() {
  clearTimeout(timer);
  timer = undefined;
  text('play', 'Play replay');
}
function showStep(index) {
  selected = index;
  const row = replay.steps[index];
  text('before-label', `Decision ${row.step} · ${row.event || row.tool}`);
  const path = `/watch/replay/${replay.run_id}/`;
  showFrame('before', row.frame_path && path + row.frame_path, 'before-note', '', 'Decision frame unavailable.');
  showFrame('after', row.after && path + row.after, 'after-note', '', 'No fresh post-action frame recorded.');
  [...byId('steps').children].forEach((button, i) => button.setAttribute('aria-pressed', String(i === index)));
}
function nextStep() {
  if (selected + 1 >= replay.steps.length) return pause();
  showStep(selected + 1);
  timer = setTimeout(nextStep, 1400);
}
byId('play').onclick = () => {
  if (timer) return pause();
  if (selected === replay.steps.length - 1) showStep(0);
  text('play', 'Pause replay');
  timer = setTimeout(nextStep, 1400);
};
function showReplay(nextReplay) {
  if (nextReplay?.run_id === replay?.run_id) return;
  pause();
  replay = nextReplay;
  const steps = byId('steps');
  steps.replaceChildren();
  byId('play').hidden = !replay;
  if (!replay) {
    text('recorded-title', 'Corner preview');
    text('replay-run', 'Frames from the scripted rehearsal.');
    text('before-label', 'Before the turn');
    showFrame('before', '/assets/before.png', 'before-note');
    showFrame('after', '/assets/after.png', 'after-note');
    return;
  }
  text('recorded-title', 'Recorded replay');
  text('replay-run', 'Completed scripted rehearsal · Play or select a decision.');
  replay.steps.forEach((row, index) => {
    const button = document.createElement('button');
    button.textContent = `${row.step} · ${row.event || row.tool}`;
    button.onclick = () => { pause(); showStep(index); };
    steps.append(button);
  });
  showStep(0);
}
async function refresh() {
  try {
    if (document.hidden) return;
    const response = await fetch('/watch/status', {signal: AbortSignal.timeout(2200)});
    if (!response.ok) throw Error();
    const state = await response.json();
    running = state.running;
    byId('live-views').hidden = !running;
    byId('recorded').hidden = running;
    text('status', state.label);
    text('status-detail', running ? `${state.decisions} decisions · ${state.collisions} collisions`
      : state.replay ? 'The latest completed judge rehearsal is available below.'
      : 'The arena preview is available while you wait.');
    if (running) pause();
    else showReplay(state.replay);
  } catch {
    pause();
    text('status', 'Connection interrupted');
    text('status-detail', 'Waiting for the rover dashboard to reconnect.');
  } finally { setTimeout(refresh, document.hidden ? 1500 : 450); }
}
startFeed('front', '/watch/front', () => running);
startFeed('spectator', '/watch/spectator', () => running);
refresh();
