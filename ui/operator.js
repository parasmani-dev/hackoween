const apiPrefix = document.querySelector('meta[name="rover-api"]')?.content ?? "";
const juryToken = document.querySelector('meta[name="jury-token"]')?.content;
function roverFetch(path, options = {}) {
  if (!apiPrefix) return fetch(path, options);
  return fetch(apiPrefix + path, {
    ...options,
    headers: { ...options.headers, "X-Jury-Token": juryToken },
  });
}

const mock = new URLSearchParams(location.search).has("mock");
const byId = (id) => document.getElementById(id);
const command = byId("command");
const start = byId("start");
const reset = byId("reset");
const status = byId("status");
const camera = byId("camera");
const cameraNote = byId("camera-note");
const viewToggle = byId("view-toggle");
const cameraTitle = byId("camera-title");
let wholeRover = false;

viewToggle.addEventListener("click", () => {
  wholeRover = !wholeRover;
  viewToggle.setAttribute("aria-pressed", String(wholeRover));
  viewToggle.textContent = wholeRover ? "Rover camera" : "Whole-rover view";
  cameraTitle.textContent = wholeRover ? "Whole-rover view" : "Front camera";
  camera.alt = wholeRover ? "Overhead view of the rover and room" : "Rover front camera";
  camera.hidden = true;
  cameraNote.textContent = mock ? "Mock mode uses a camera placeholder." : "Loading camera…";
});

if (mock) {
  byId("mode").textContent = "Mock run · no rover connected";
  const phases = ["Locate", "Go to object", "Pick up", "Carry", "Drop", "Done"];
  let phase = -1;
  let activeCommand = "";
  function render() {
    start.disabled = phase !== -1;
    command.disabled = phase !== -1;
    reset.disabled = phase !== phases.length - 1;
    const state = phases[phase] ?? "Idle";
    const result = state === "Done" ? " · mock pass" : "";
    status.textContent = activeCommand ? `${state} · ${activeCommand}${result}` : state;
    cameraNote.textContent = "Mock mode uses a placeholder for the camera.";
  }
  byId("controls").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = command.value.trim();
    if (!value || phase !== -1) return;
    activeCommand = value;
    phase = 0;
    render();
    const timer = setInterval(() => {
      phase += 1;
      render();
      if (phase === phases.length - 1) clearInterval(timer);
    }, 700);
  });
  reset.addEventListener("click", () => {
    if (phase !== phases.length - 1) return;
    phase = -1;
    activeCommand = "";
    command.value = "";
    render();
    command.focus();
  });
  render();
} else {
  byId("mode").textContent = "Live engine · Start prepares the rover. Launch Claude separately.";
  const feedback = byId("feedback");
  let roverState = null;
  let busy = false;
  let revision = 0;
  let offlineDemo = null;

  async function readOfflineDemo() {
    const response = await roverFetch("/offline-demo", { cache: "no-store" });
    return response.ok ? await response.json() : null;
  }

  function updateControls() {
    command.disabled = busy || roverState !== "Idle" || Boolean(offlineDemo?.enabled);
    start.disabled = busy || roverState !== "Idle";
    reset.disabled = busy || offlineDemo?.status === "running" || (roverState !== "Done" && roverState !== "Locate");
  }

  async function changeRun(path, args) {
    busy = true;
    revision += 1;
    updateControls();
    try {
      const response = await roverFetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(args),
      });
      if (response.status === 409) {
        feedback.textContent = "The engine rejected this action in its current state.";
        return;
      }
      if (!response.ok) throw new Error("Lifecycle request failed");
      const { state } = await response.json();
      roverState = state;
      status.textContent = state;
      feedback.textContent = state === "Locate"
        ? (offlineDemo?.enabled ? "Scripted fetch running." : "Engine ready. Launch the agent with the same command.")
        : "Rover reset.";
      if (state === "Idle") command.value = offlineDemo?.enabled ? offlineDemo.command : "";
    } catch {
      feedback.textContent = "Action could not be confirmed. Check rover status before retrying.";
    } finally {
      revision += 1;
      busy = false;
      updateControls();
    }
  }

  byId("controls").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = command.value.trim();
    if (!busy && roverState === "Idle" && value) changeRun("/run/start", { command: value });
  });
  reset.addEventListener("click", () => {
    if (!reset.disabled) changeRun("/run/reset", {});
  });
  updateControls();
  let displayedFrame = null;
  let cameraFailures = 0;
  async function refreshCamera() {
    let nextFrame = null;
    const started = performance.now();
    const requestedWholeRover = wholeRover;
    try {
      if (document.hidden) return;
      const response = await roverFetch(requestedWholeRover ? "/spectator" : (offlineDemo?.preview ? "/preview" : "/look"), {
        cache: "no-store", signal: AbortSignal.timeout(3000),
      });
      if (!response.ok) throw new Error("Camera unavailable");
      nextFrame = URL.createObjectURL(await response.blob());
      const decoded = new Image();
      decoded.src = nextFrame;
      await decoded.decode();
      if (requestedWholeRover !== wholeRover) return;
      camera.src = nextFrame;
      if (displayedFrame) URL.revokeObjectURL(displayedFrame);
      displayedFrame = nextFrame;
      nextFrame = null;
      camera.hidden = false;
      cameraFailures = 0;
      cameraNote.textContent = "";
    } catch {
      if (requestedWholeRover !== wholeRover) return;
      cameraFailures += 1;
      if (cameraFailures >= 3) cameraNote.textContent = !camera.hidden
        ? "Camera unavailable or stale - showing the last received frame."
        : "Camera unavailable - waiting for the first frame.";
    } finally {
      if (nextFrame) URL.revokeObjectURL(nextFrame);
      // One request/decode at a time. Hidden tabs do not poll the camera.
      setTimeout(refreshCamera, document.hidden ? 1000 : Math.max(0, 50 - (performance.now() - started)));
    }
  }
  refreshCamera();
  async function refresh() {
    const observedRevision = revision;
    try {
      const response = await roverFetch("/state", { cache: "no-store", signal: AbortSignal.timeout(4000) });
      if (!response.ok) throw new Error("State unavailable");
      const state = await response.json();
      const demo = await readOfflineDemo();
      if (busy || observedRevision !== revision) return;
      roverState = state.state;
      offlineDemo = demo;
      if (demo?.enabled) {
        byId("mode").textContent = apiPrefix ? "Offline scripted rehearsal" : "Scenario 1: Red sphere delivery";
        byId("scenario-switch").hidden = !apiPrefix && window.location.port !== "8112";
        command.value = demo.command;
        feedback.textContent = {
          idle: "", running: "Scripted fetch running.", pass: "PASS: Red sphere delivered.",
          fail: "FAIL: Check the local driver log.",
        }[demo.status];
      }
      if (roverState !== "Locate" && feedback.textContent === "Engine ready. Launch the agent with the same command.") {
        feedback.textContent = "";
      }
      updateControls();
      const phaseLabel = roverState === "Done" ? "Run ended" : roverState;
      const summary = `${phaseLabel} · Holding: ${state.holding ? "yes" : "no"} · Retries: ${state.retry_count}`;
      if (status.textContent !== summary) status.textContent = summary;
    } catch {
      if (busy || observedRevision !== revision) return;
      if (roverState === null) {
        updateControls();
        status.textContent = "Waiting for the rover engine.";
      }
    } finally {
      setTimeout(refresh, document.hidden ? 1000 : 300);
    }
  }
  refresh();
}
