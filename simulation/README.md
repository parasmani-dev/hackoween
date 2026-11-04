# Simulator

Open `simulation/worlds/fetch_room.wbt` in Webots R2025a or later and press Play. Inspect the rover camera overlay and confirm the four walls, six objects and green drop zone. The preview controller only enables the camera and advances the scene for inspection. The command-driven controller will replace it in Phase 2.

The world uses ENU coordinates with Z up. The rover begins facing +X. The four wheels are visual geometry in this preview. Static checks cover scene structure and camera configuration. Rendering, collision behavior and fetch actions require Webots runtime verification.

On Windows, run the camera smoke check from the repository root with the path to your Webots executable.

```powershell
./simulation/verify_scene.ps1 -WebotsExe 'C:/Program Files/Webots/msys64/mingw64/bin/webots.exe'
```

The check opens a temporary copy of the world and saves a 640×480 camera frame and Webots logs under `runs/`. Inspect the saved frame for the room, objects, and drop zone. Movement, pickup, and collision checks need the Phase 2 controller.

## Scripted fetch rehearsal

From the repo root, launch the visible Webots rehearsal in PowerShell.

```powershell
.\simulation\run_fetch_demo.ps1
```

Use `-WebotsExe` to point at another Webots installation. Use `-Fast` for a batch check. The launcher prints turn, drive, pickup, and drop events followed by PASS or FAIL. Each run uses a temporary world copy and keeps its logs under `runs/`.

The rehearsal uses a fixed target and route and moves the rover through Webots Supervisor poses. It checks pickup reach and angle and verifies the sphere's final location after release. Motor-driven movement, collision recovery, the engine bridge, and Claude perception are separate from this rehearsal.
