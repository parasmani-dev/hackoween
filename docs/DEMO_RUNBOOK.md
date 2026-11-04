# Rover demo and submission runbook

## Offline rehearsal

Use the simulator branch from PR #6, engine `7193439` from PR #7, agent `bcb262d` from PR #8, and UI and recording `56eeb14` from PR #9. Check out teammates' branches in separate folders. Install Webots R2025a, Python with Pillow, and Node.js. The recording scripts also need FFmpeg with drawtext and ffprobe.

From the simulator checkout in PowerShell, set these paths to your local checkouts.

```powershell
$engine = (Resolve-Path '..\hackoween-engine-test\engine\server.py').Path
$agent = (Resolve-Path '..\hackoween-agent-test').Path
$ui = (Resolve-Path '..\hackoween-ui-test\ui\server.mjs').Path
$webots = 'C:\Program Files\Webots\msys64\mingw64\bin\webots.exe'
.\simulation\check_http_fetch.ps1 -EngineServer $engine -WebotsExe $webots -UiServer $ui
```

The check starts Webots, the HTTP engine on port 8080, and the UI proxy on port 18090. It sends Start and Reset through the UI proxy and uses a scripted motor-driven route for the red sphere. It verifies the camera, in-zone delivery, private verdicts, collision recovery, retries, and native capture. It prints the manifest path. Ports 8080, 18090, and 18765 must be available. This rehearsal uses zero model calls.

## November 5 at 9:00 AM IST

1. Paras gets the organizer's key, exact Claude model identifier, and playback timing ruling. Record the timing ruling in issue #5. Keep the existing 31 FPS clip labeled as a rehearsal until the ruling is available.
2. Archit runs one red-sphere command with recording and request auditing enabled. Use the launcher as the single owner of Start. This live path runs separately from the offline UI rehearsal. Keep credentials in the local shell and agree on the decision cap before making calls.

```powershell
$env:ANTHROPIC_API_KEY = Read-Host 'Organizer Claude API key' -MaskInput
$env:CLAUDE_MODEL = Read-Host 'Organizer Claude model identifier'
.\simulation\check_http_fetch.ps1 -EngineServer $engine -WebotsExe $webots -AgentRoot $agent -Provider claude -Command 'Fetch the red sphere and drop it in the green zone' -ExpectedFixtureKey item_1 -RecordPov
```

3. Paras checks the observed delivery and `operator-result.json`. Archit checks the tool history and model requests. Ayush verifies that the 640×480 decision images match the logged PNGs and that native 1280×960 POV and private verdicts stay separate. Record only sanitized results in issue #5.
4. Once the red sphere passes, repeat with blue cylinder and yellow cone. Investigate the first divergence before another run if the rover misses a target.

## Submission checklist

- [ ] Review and integrate the four PRs and verify a fresh recursive checkout.
- [ ] Verify three hands-free live commands and record the required organizer sign-off.
- [ ] Run each of the three hidden commands once at the organizer's scheduled time.
- [ ] Export all three clips at 720p or higher and 10–20 seconds, with the exact command visible for the first two seconds. Check that there are no team names, logos, or code in the footage. Preserve raw capture timing.
- [ ] Submit the three clips through the organizer's feedback API by the stated deadline.
- [ ] Submit the repository link, each hidden run's tool-call log, and a paragraph explaining how Claude identifies objects from camera pixels. Book the live demo slot.

Suggested submission paragraph

> The agent sends Claude the typed fetch command and the rover's front-camera image. Claude uses the visible shape, colour, and position in the image to choose a movement, turn, pickup, or drop. After movement or turning, the next decision uses a fresh camera image. Object identities and delivery verdicts stay in the private evaluator.
