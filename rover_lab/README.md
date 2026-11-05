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
