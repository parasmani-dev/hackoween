const mock = new URLSearchParams(location.search).has("mock");
const byId = (id) => document.getElementById(id);
const command = byId("command");
const start = byId("start");
const reset = byId("reset");
const status = byId("status");
const camera = byId("camera");
const cameraNote = byId("camera-note");

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
    const response = await fetch("/offline-demo", { cache: "no-store" });
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
      const response = await fetch(path, {
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
  camera.addEventListener("load", () => {
    camera.hidden = false;
    cameraNote.textContent = "";
  });
  camera.addEventListener("error", () => {
    camera.hidden = true;
    cameraNote.textContent = "Camera unavailable.";
  });
  async function refresh() {
    const observedRevision = revision;
    camera.src = `/look?t=${Date.now()}`;
    try {
      const response = await fetch("/state", { cache: "no-store" });
      if (!response.ok) throw new Error("State unavailable");
      const state = await response.json();
      const demo = await readOfflineDemo();
      if (busy || observedRevision !== revision) return;
      roverState = state.state;
      offlineDemo = demo;
      if (demo?.enabled) {
        byId("mode").textContent = "Offline scripted fetch";
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
      roverState = null;
      updateControls();
      status.textContent = "Engine status unavailable. Start the engine on localhost:8080.";
    } finally {
      setTimeout(refresh, 1000);
    }
  }
  refresh();
}
