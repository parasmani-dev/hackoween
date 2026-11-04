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

The bridge binds only to loopback. The launcher defaults to port 18765 for the check. The production adapter and bridge default to 8765. Depth is optional and currently unavailable. Use engine revision 7193439 (PR #7) or a compatible later revision with operator finish and private verdicts.

## Live model run

Pass Vernit's server and Archit's checkout to the same launcher. Set the selected provider's key and model in your local shell. Claude uses ANTHROPIC_API_KEY and CLAUDE_MODEL. OpenAI uses OPENAI_API_KEY and OPENAI_MODEL.

```powershell
.\simulation\check_http_fetch.ps1 -EngineServer '<engine-checkout>\engine\server.py' -AgentRoot '<agent-checkout>' -Provider openai -Command 'Fetch the red sphere and drop it in the green zone'
```

The launcher starts the operator run and agent, then keeps request audits, frame logs, and the final camera image in a unique attempt directory under runs/live. Pass -ExpectedFixtureKey for a known test fixture. The launcher calls the operator finish endpoint after the agent exits and saves the verdict separately in operator-result.json. Keep that file and request audits local.


## Native operator POV capture

Pass -RecordPov to the live launcher to record from HTTP Start through Finish.
The same front camera renders at 1280×960 and the bridge resizes the agent
JPEG to 640×480. Install Pillow in the Python environment Webots uses.

The scripted HTTP check captures a full fixture automatically and prints its
manifest path. Each capture is stored separately under the temporary Webots
project's pov/<run-id> directory, with numbered native PNGs and capture.json.
The manifest records the command, relative frame paths, elapsed simulation
milliseconds, and UTC wall times. Reset abandons an active capture without
publishing a manifest. Completed captures are preserved. The live launcher
writes the manifest path in its private operator-result.json.
