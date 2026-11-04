# Simulator

Open `simulation/worlds/fetch_room.wbt` in Webots R2025a or later and press Play. Inspect the rover camera overlay and confirm the four walls, six objects and green drop zone. The preview controller only enables the camera and advances the scene for inspection. The command-driven controller will replace it in Phase 2.

The world uses ENU coordinates with Z up. The rover begins facing +X and has four powered wheel joints. The preview controller leaves the rover parked.

On Windows, run the camera smoke check from the repository root with the path to your Webots executable.

```powershell
./simulation/verify_scene.ps1 -WebotsExe 'C:/Program Files/Webots/msys64/mingw64/bin/webots.exe'
```

The check opens a temporary copy of the world and saves a 640Ã—480 camera frame and Webots logs under `runs/`. Inspect the saved frame for the room, objects, and drop zone. Use the fetch and motor checks below to exercise motion and pickup.

## Scripted fetch rehearsal

From the repo root, launch the visible Webots rehearsal in PowerShell.

```powershell
.\simulation\run_fetch_demo.ps1
```

Use `-WebotsExe` to point at another Webots installation. Use `-Fast` for a batch check. The launcher prints turn, drive, pickup, and drop events followed by PASS or FAIL. Each run uses a temporary world copy and keeps its logs under `runs/`.

The rehearsal drives the physical wheel motors along a fixed route. It checks pickup reach and angle, carries the sphere with a simulated snap attachment, and lets it settle under physics after release. The final sphere location determines PASS or FAIL. The engine bridge and Claude perception are separate from this rehearsal.

## Physical motor and wall recovery check

```powershell
.\simulation\run_fetch_demo.ps1 -MotorCheck
```

The rover reverses into the west wall, stops, backs away by 0.1 m, turns clockwise, and drives clear. PowerShell reports the measured backoff, turn, and recovery travel. Add `-Fast` to run in batch mode.

`RoverDrive` uses Webots wheel velocity commands and measures progress from the rover's own pose. Drive actions stop on chassis contact, two seconds without meaningful progress, or a bounded timeout. This standalone check exercises one recovery sequence. Retry accounting and integration with the engine belong to the bridge work.

## Full fetch through HTTP

From the repository root, with Pillow installed, pass the path to Vernit's engine server checkout. The launcher starts a temporary Webots world, uses the real bridge with the engine's create_server factory, and checks the fetch and wall collision via HTTP. The fixture uses HTTP start and reset, then verifies collision backoff and retry exhaustion.

```powershell
.\simulation\check_http_fetch.ps1 -EngineServer '..\engine-checkout\engine\server.py'
```

The bridge binds only to loopback. The launcher defaults to bridge port 18765 and HTTP engine port 8080. Use `-EnginePort <port>` for an explicitly isolated offline rehearsal; the UI proxy follows that engine port. `-BridgePort` selects the bridge port separately. The production adapter and bridge default to 8765. Depth is optional and currently unavailable. Use engine revision 7193439 (PR #7) or a compatible later revision with operator finish and private verdicts.

## Live model run

Pass Vernit's server and Archit's checkout to the same launcher. Set the selected provider's key and model in your local shell. Claude uses ANTHROPIC_API_KEY and CLAUDE_MODEL. OpenAI uses OPENAI_API_KEY and OPENAI_MODEL.

```powershell
.\simulation\check_http_fetch.ps1 -EngineServer '<engine-checkout>\engine\server.py' -AgentRoot '<agent-checkout>' -Provider claude -Command 'Fetch the red sphere and drop it in the green zone' -ExpectedFixtureKey item_1 -MaxDecisions 25 -RecordPov
```

The launcher starts the operator run and agent, then keeps request audits, frame logs, and the final camera image in a unique attempt directory under runs/live. The command-driven acceptance launcher requires `-ExpectedFixtureKey` for the privately selected test fixture and an explicitly authorized `-MaxDecisions` cap. It validates these plus provider configuration before creating a world or starting Webots. The key never enters model input. Do not run the example until model calls are authorized. The launcher calls the operator finish endpoint after the agent exits and saves the verdict separately in operator-result.json. Keep that file and request audits local.


## Native operator POV capture

Pass -RecordPov to request engine-owned capture from Idle exit through the first Done.
The same front camera renders at 1280×960 and the bridge resizes the agent
JPEG to 640×480. Install Pillow in the Python environment Webots uses.

The scripted HTTP check captures a full fixture automatically and prints its
manifest path. Each capture is stored separately under the temporary Webots
project's pov/<run-id> directory, with numbered native PNGs and capture.json.
The manifest records the command, relative frame paths, elapsed simulation
milliseconds, and UTC wall times. Reset abandons an active capture without
publishing a manifest. Completed captures are preserved. The live launcher
writes the manifest path in its private operator-result.json.

## Offline timing audit

Run `check_http_fetch.ps1` with `-MeasureTiming` (no `-Command`). It measures a
frozen wait/read interval, a 0.5 m move, a 90-degree turn, successful pickup and
an out-of-zone release, then runs the existing full HTTP suite. Fixed fixture
waypoints stay inside this test; no model client is launched. `runs/timing-*/measurements.json`
records simulation time, wall time, capture cadence, and measured rover motion.
It does not certify wall-clock render FPS, exact constant motor speed, or approval
for retiming submission videos.

The rover carries objects at floor level using a snap attachment; pickup sets the
attachment and drop detaches at the existing position, without advancing physics
or moving XY into the zone. There is no lift/fall animation. Attachment updates
happen before motion rendering so the final camera frame represents the release
position. Reset may warm/flush physics outside active runs and captures.

Wheel braking removes post-command coasting. The turn controller compensates for
skid steering using measured rover yaw. Regression tolerances are 15 mm travel,
3 degrees heading and 150 ms turn-duration error; these are engineering test
limits, not an organizer waiver of the specified nominal 1 m/s and 90 degrees/s.
`-MeasureTiming` covers forward/reverse 0.5 m, 0.1/1.0 m, and +/-90/45/180 degrees.

Native PNG encoding uses two workers and at most eight pending frames. Camera
bytes are copied on the controller thread; finish waits for all writes and never
publishes a complete manifest on a writer error. Simulation and wall clocks remain
unchanged in the manifest. Submission playback policy is still an external gate.

## Engine-owned recording and offline agent rehearsal

Recording-enabled launch now requires the engine capture contract from PR #18.
The launcher sends `record_pov: true` on private Start and reads capture ID/status/
manifest only from private Finish. It never starts or finalizes a second capture.
The engine owns Idle-to-Locate and terminal capture boundaries.

Use `-OfflineAgentCheck` without `-Command` to run the real agent loop with a fixed
scripted policy through HTTP and engine-owned recording after the usual checks.
Both provider transports are patched to reject calls. Artifacts are under
`runs/offline-agent/`, marked `execution_mode: offline_scripted`; these are not
Claude acceptance or hidden-test evidence.

## Private provenance and hidden attempts

Each command-driven attempt writes provider, configured model, decision cap,
command hash and attempt ID to private `provenance.json` before Start. The final
private operator result also binds agent run and capture IDs. Credentials never
enter those files. For authorized official commands only, supply `-OfficialSlot`
(1, 2 or 3) and `-OfficialLedger <shared persistent directory>` plus `-RecordPov`.
All three runs must use the same ledger on the designated submission laptop,
across checkouts. A slot is claimed exclusively before Start; a second claim is
rejected, including after failure. Claims are not automatically deleted. This
local safeguard cannot enforce attempts made on other computers or another ledger.
Do not use real official slots in rehearsals. No model call is authorized merely
by these instructions.

## Smooth operator preview

Set `ROVER_PREVIEW_FILE` to the same absolute JPEG path in the Webots and UI
server environments. The controller publishes a 640x480 operator-only preview
after each motion step; the UI serves it at `/preview` without waiting for the
engine action lock. Agent `/look` and recordings remain unchanged. The browser
requests at most 20 frames/second with one fetch/decode at a time, pauses camera
polling in hidden tabs, and retains its last good frame during transient errors.
Without the variable, the UI falls back to the original `/look` endpoint.

For a persistent real-adapter engine, launch the bridge world then run
`python -m integration.serve_preview --bridge-port <port> --engine-port <port>`.
Run `node ui/server.mjs` with matching `ROVER_ENGINE_PORT`, `ROVER_UI_PORT`, and
`ROVER_PREVIEW_FILE`. `ROVER_OFFLINE_DEMO=1` enables the fixed offline Start route;
no model is launched by this preview setup.
