function startFeed(id, path, active = () => true) {
  const target = document.getElementById(id);
  const note = document.getElementById(id + '-state');
  let displayed;
  async function refresh() {
    const started = performance.now();
    let candidate;
    let retryDelay = 0;
    try {
      if (document.hidden || !active()) return;
      const response = await fetch(path, {signal: AbortSignal.timeout(2000)});
      if (!response.ok) throw Error('Feed unavailable');
      candidate = URL.createObjectURL(await response.blob());
      const image = new Image();
      image.src = candidate;
      await image.decode();
      target.src = candidate;
      if (displayed) URL.revokeObjectURL(displayed);
      displayed = candidate;
      candidate = null;
      note.textContent = response.headers.get('X-Frame-State') === 'paused'
        ? 'Paused · last simulator frame' : 'Live feed';
    } catch {
      retryDelay = 150;
      note.textContent = displayed
        ? 'Feed interrupted · last frame shown' : 'Feed interrupted · waiting for a frame';
    } finally {
      if (candidate) URL.revokeObjectURL(candidate);
      // Keep the request and decode inside the frame budget, with one frame in flight.
      const frameInterval = 1000 / 30;
      const delay = document.hidden || !active()
        ? 750 : Math.max(retryDelay, frameInterval - (performance.now() - started));
      setTimeout(refresh, delay);
    }
  }
  refresh();
}
function showFrame(id, path, noteId, label = '', empty = '') {
  const image = document.getElementById(id);
  const note = document.getElementById(noteId);
  if (image.dataset.framePath === (path || '') && note.dataset.label === (path ? label : empty)) return;
  image.dataset.framePath = path || '';
  note.dataset.label = path ? label : empty;
  image.onload = () => { note.textContent = label; };
  image.onerror = () => {
    image.removeAttribute('src');
    note.textContent = 'Recorded frame unavailable.';
  };
  note.textContent = path ? label : empty;
  if (path) image.src = path;
  else image.removeAttribute('src');
}
