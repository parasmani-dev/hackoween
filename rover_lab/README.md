# Rover Lab

Rover Lab hosts both offline Webots scenarios on one local site. Scenario 1 delivers the red sphere. Scenario 2 provides the blue-cylinder jury layouts, cue swap, collision rehearsal, and spectator replay. Both use scripted decisions and real Webots camera feeds.

Install Webots, Node.js, PowerShell 7.4 or later, and Python. Install Pillow into the Python environment used by the terminal and by Webots.

```powershell
python -m pip install -r agent/requirements.txt
pwsh -File rover_lab/start.ps1 -WebotsExe 'C:\Program Files\Webots\msys64\mingw64\bin\webots.exe'
```

Open [Rover Lab](http://127.0.0.1:8113/) and choose a scenario. The launcher prints process IDs and the runtime directory. Stop those processes when you finish. If the default ports are occupied, use `-PortOffset 100` and open the URL printed by the launcher.

From the repository root, verify both scenarios and generate the offline report.

```powershell
$env:ROVER_LAB_URL = 'http://127.0.0.1:8113'
$env:ROVER_LAB_RUN_DIR = Join-Path (Get-Location) 'runs/rover-lab-8113'
python rover_lab/verify.py
```

For a port offset, use the URL and runtime directory printed by the launcher. Rover Lab writes generated evidence, camera frames, and simulator logs under `runs/`. The site includes small preview images from a scripted run and Geist fonts under their [SIL Open Font License](site/LICENSE.txt).
The offline verifier allows 150 seconds per rehearsal by default; use
`ROVER_LAB_VERIFY_TIMEOUT` to change that test deadline on a slower machine.
It does not alter engine action timeouts or model decision caps.

## Claude Haiku 4.5 in the new UI

Scenario 1 at `/scenario-1` includes offline and live modes. The default launcher
keeps live mode disabled. Scenario 2 remains an offline scripted rehearsal.
To enable Haiku, set `ANTHROPIC_API_KEY` in the local environment or `.env` and run:

```powershell
./rover_lab/start.ps1 -WebotsExe C:/path/to/webots.exe -EnableLiveAgent
```

Use `-EnvFile C:/path/to/.env` for an environment file outside this checkout.
The Haiku client pins `claude-haiku-4-5-20251001` without changing `CLAUDE_MODEL`
or the OpenAI path. Only the dashboard process receives the Claude credential.
Enabling the launcher makes no API calls. On Scenario 1 select live mode,
enter a command and a decision cap (1–60, default 25), then press Start Haiku fetch.
The server owns Start, agent execution and Finish. Reset and overlapping starts
are blocked during a run. The page shows the latest five actions and final verdict.
Live model requests, JSONL/PNG artifacts and private capture metadata are saved
under the printed runtime directory's `live/attempt-*` folder. Credentials and
private evaluator/capture fields never enter browser status or model requests.

Before a paid run, run the unit regressions and the offline live-path verifier:

```powershell
python -B -m unittest agent.test_clients agent.test_runner agent.test_affordances rover_lab.test_live -v
# Start Rover Lab WITHOUT -EnableLiveAgent, then use its printed ports:
python -B -m rover_lab.verify_live_offline --engine-port 18836 --ui-port 8112 --run-dir runs/haiku-ui-offline
python -B -m rover_lab.verify_navigation_offline --engine-port 18836 --output runs/navigation-camera-offline
```

For `-PortOffset 300`, the two verifier ports are 19136 and 8412. The verifier
requires an idle test engine and resets it. It starts a temporary copy of the new
UI server and mocks the Claude API transport, exercises authenticated Start,
status, reset, duplicate-start guards and the real camera/engine/recording path,
then verifies private delivery and exact model-frame matches. Its fixed fixture
waypoints stay inside the verifier; zero provider calls and zero tokens are used.
Artifacts are labeled `offline_scripted_transport_stub`. This proves integration,
not autonomous Haiku navigation. The navigation prompt now calibrates small turns
to image positions, separates pickup from zone search and handles close targets;
the runner also rejects immediate repeat moves into a collision. Live delivery
must be assessed separately after an authorized paid run.

After a failed pickup/drop, that action is temporarily removed from both
providers' offered tools until the rover's actual pose changes. A fallback
guard also rejects repeated proposals from injected policies. Zero/ineffective
motion does not unlock another attempt; three rejected proposals stop the loop.
Only the same four public action schemas are used. Camera geometry explains why
a target touching the image bottom can still be beyond 0.5 m center-based reach;
the prompt uses shorter 0.05 m final approaches rather than repeated pickup at
an unchanged pose. The verifier deliberately starts with a failed pickup and
checks suppression, corrected motion and eventual scripted delivery.

The UI reports preparing, decision progress and finalization separately. Reload
restores the latest result within the current server session. Reset clears the
displayed actions while preserving disk evidence. Finish and recording failures
remain explicit instead of appearing as a successful delivery.

The separate camera-calibration verifier uses red connected components as an
offline measurement instrument, not as production perception. It checks the
turn sign against camera pixels, aligns again after each short approach, and
verifies actual pickup without using private target coordinates. It finishes
and resets the test engine. This is a pickup rehearsal, not a full delivery or
evidence of autonomous Haiku decisions.

## Forward clearance before pickup

The new scenes include one physical forward range ray, 0.27 m ahead of the rover
center at 0.17 m height. The launcher opts into `/depth`; older simulator adapters
keep depth unavailable. The engine serializes range reads with motion and returns
only a finite metre distance (0–10) or null, never object metadata.

Before dispatch, the agent rejects positive moves exceeding the latest measured
range minus 0.04 m clearance. Rejections are logged without movement or a new
image. The prompt asks for direct alignment and a pickup trial before contact.
No action is silently shortened in production. The offline measurement verifier
selects its own shorter move within that limit.

This is a centerline clearance guard, not full collision avoidance. Side contacts
still require camera-based route selection. A carried object occludes the ray,
so depth is unavailable while holding; camera navigation remains in use. Real
collision physics and the private evaluator are unchanged. No collision-free
live success is implied by an offline pass.
