# Rover Fetch · Rover Lab

A simulated rover that finds an object, picks it up, and delivers it to a marked
drop zone. Rover Lab provides a local operator interface with front-camera and
spectator views, delivery results, and decision replay.

The project includes two ways to run the rover:

- **Offline rehearsal** uses scripted decisions and real Webots physics. It
  exercises delivery, layout changes, wrong-object detection, and recovery
  without a model API key.
- **Vision agent** sends the typed command and camera images to Claude or
  OpenAI, validates each proposed action, and executes it through the engine.
  This mode uses the configured provider API.

The submission overview includes the required explanation of how Claude
identifies objects from images. [Submission overview](SUBMISSION.md)

## Quick start on Windows

### Prerequisites

- Git with submodule support.
- Python 3.10 or newer with Pillow installed.
- Webots R2025a.
- Node.js 20 or newer.
- PowerShell 7.4 or newer, available as `pwsh`.
- FFmpeg with `drawtext` and `libx264`, plus ffprobe, for clip export and
  submission validation.

Webots must use a Python interpreter with Pillow installed. The steps below
install the agent dependencies into the terminal's current Python environment.

```powershell
git clone --branch feat/archit-agent --recurse-submodules https://github.com/parasmani-dev/hackoween.git
Set-Location hackoween
python -m pip install -r agent/requirements.txt
$webots = 'C:\Program Files\Webots\msys64\mingw64\bin\webots.exe'
pwsh -File rover_lab/start.ps1 -WebotsExe $webots
```

Set `$webots` to your installed executable. The launcher starts both Webots
scenarios, their engine services, and the local site. Open
[Rover Lab](http://127.0.0.1:8113/) in your browser.

### Try the offline scenarios

1. Open **Scenario 1** for the red-sphere delivery. Click Start and watch the
   front-camera or whole-rover view. Reset to repeat.
2. Open the **Jury Demo** for Scenario 2. Select a layout and run the
   blue-cylinder command. Layouts A, B, and C change the approach or the cues
   used to identify the target.
3. Use the wrong-object and collision probes to inspect failure and recovery
   behaviour. The jury console shows the private verdict and decision evidence.
4. Open **Watch Demo** for the spectator display and replay.

The offline command field and supported examples reflect each scenario's
scripted route. The vision agent handles model-driven commands through its
separate operator-started run.

The launcher prints each process ID. To stop the demo, stop those printed
processes in PowerShell with `Stop-Process -Id <PID>`. The services keep running
after the launcher returns.

If a port is occupied, launch with `-PortOffset 100` and use the printed site
URL, normally `http://127.0.0.1:8213/`. Use the same offset in the verification
environment below. [Rover Lab setup](rover_lab/README.md)

## Verify the offline demo

Keep the launched services running, then execute from the repository root:

```powershell
$env:ROVER_LAB_URL = 'http://127.0.0.1:8113'
$env:ROVER_LAB_RUN_DIR = Join-Path (Get-Location) 'runs/rover-lab-8113'
python -B rover_lab/verify.py
```

The verifier runs both scenarios and checks layout selection, delivery in
layouts A/B/C, deliberate wrong-object rejection, collision recovery, camera
feeds, and replay evidence. It writes `offline-report.json` in the selected
runtime directory. These checks use scripted decisions and make zero model
calls.

Run the agent and engine unit tests independently:

```powershell
python -B -m unittest discover -s agent -v
python -B -m unittest discover -s engine -v
```

## Run the vision agent

A model-driven run requires the selected provider's key and model configuration,
a running engine, and an operator-started command. Start the engine run once
before launching the agent with the same command.

For Claude, configure `ANTHROPIC_API_KEY` and `CLAUDE_MODEL` in the environment.
For OpenAI, configure `OPENAI_API_KEY` and optionally `OPENAI_MODEL`.

```powershell
python -m agent 'Fetch the red sphere and drop it in the green zone' --provider claude --max-decisions 25 --audit-requests
```

This command invokes the provider API. Use an authorized model and decision
budget. The integrated launcher can manage Start, recording, request auditing,
and private evaluator results together. [Agent setup](agent/README.md) ·
[Integrated runbook](docs/DEMO_RUNBOOK.md)

## How the components fit together

| Component | Role |
| --- | --- |
| `rover_lab/` | Combined local site, jury layouts, scenario launch, and offline verification |
| `simulation/` | Webots world, physical rover, camera, and simulator bridge |
| `engine/` | HTTP endpoints, serialized actions, run lifecycle, collision retries, and private evaluator |
| `agent/` | Claude/OpenAI clients, validated action loop, decision budget, and PNG/JSONL artifacts |
| `ui/` | Operator controls and camera proxy for the original scenario |
| `recording/` | POV export, clip validation, and submission packaging |
| `integration/` | Local service and rehearsal helpers |
| `docs/` | Shared contract, integration runbook, and readiness evidence |
| `upstream/embodify/` | Pinned upstream foundation retained with its attribution |

The engine exposes rover state, a 640 x 480 front-camera JPEG, optional
single-ray depth, and move/turn/pickup/drop actions. The agent receives
allowlisted state and action results. Private object identities, fixture
coordinates, evaluator results, and spectator images stay outside model
context. [HTTP and artifact contract](docs/CONTRACT.md)

## Logs, recording, and submission

Generated evidence is stored under `runs/`. Agent runs contain `steps.jsonl`
and numbered decision PNGs. A fresh image follows each dispatched move or turn.
Pickup, drop, and rejected proposals reuse the current frame. Optional request
audits record provider request bodies locally.

Native operator captures retain both simulation and wall-clock timestamps.
The exporter produces a command-overlay MP4, and the packager validates its
association with the original attempt and decision evidence.

Official submission requires the public repository, three hidden-command run
logs, the visual-identification paragraph, and a confirmed live demo booking.
The official clips and organizer confirmations are supplied from the scheduled
runs. [Submission overview](SUBMISSION.md) ·
[Export and packaging](recording/SUBMISSION.md)

## Team

| Owner | Area |
| --- | --- |
| Paras · @parasmani-dev | Simulation, shared integration, and merges |
| Vernit · @guptavernit2-afk | Engine and HTTP API |
| Archit · @karchit1128 | Vision agent |
| Ayush · @ayushyadav0707 | UI, recording, and submission tooling |

## Attribution

Embodify is pinned as a Git submodule at
`6be154471801f4cea47f3df1f46b000476b3eeb6`. Its original history, Apache-2.0
license, NOTICE, and third-party notices are retained.
[Upstream license](upstream/embodify/LICENSE) ·
[NOTICE](upstream/embodify/NOTICE) ·
[Third-party notices](upstream/embodify/THIRD_PARTY_NOTICES.md)

Rover Lab bundles Geist fonts under the SIL Open Font License.
[Font license](rover_lab/site/LICENSE.txt)
