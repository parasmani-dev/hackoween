# Simulator

Open `simulation/worlds/fetch_room.wbt` in Webots R2025a or later and press Play. Inspect the rover camera overlay and confirm the four walls, six objects and green drop zone. The preview controller only enables the camera and advances the scene for inspection. The command-driven controller will replace it in Phase 2.

The world uses ENU coordinates with Z up. The rover begins facing +X and has four powered wheel joints. The preview controller leaves the rover parked.

On Windows, run the camera smoke check from the repository root with the path to your Webots executable.

```powershell
./simulation/verify_scene.ps1 -WebotsExe 'C:/Program Files/Webots/msys64/mingw64/bin/webots.exe'
```

The check opens a temporary copy of the world and saves a 640×480 camera frame and Webots logs under `runs/`. Inspect the saved frame for the room, objects, and drop zone. Use the fetch and motor checks below to exercise motion and pickup.

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
