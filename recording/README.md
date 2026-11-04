# Rover POV export

Use Node.js and FFmpeg with drawtext and libx264. Put FFmpeg on PATH or set FFMPEG_PATH to its absolute executable path.

    node recording/export.mjs <frames-dir> <command.txt> <fps> <output.mp4>
    ffprobe -v error -show_entries stream=codec_name,width,height,nb_frames:format=duration -of json <output.mp4>

Supply consecutive native front-camera PNGs starting at 000000.png, captured at the declared fixed rate from leaving Idle through Done. Use a consistent resolution of at least 960×720. Store the exact typed command in a one-line UTF-8 file. Export requires a 10–20 second sequence and overlays the command for two seconds. Inspect the output for a readable, unclipped command, robot POV throughout, and the complete ending.

Store capture frames and MP4s separately from agent artifacts. The exporter reads only local files. Webots provides native POV capture at simulator revision 0d19beb. Mentor approval for inference pauses and playback speed is pending.

## Operator capture fixture

The proposed capture format stores the exact command and frame list in capture.json.
Version 1 has fields version, run_id, command, and frames. Each frame entry
has path (frames/000000.png onward), sim_time_ms (elapsed simulation time),
and wall_time_utc (UTC ISO 8601 with milliseconds). Both clocks are nondecreasing.
Publish the manifest after completing the frame sequence. Keep the capture
separate from Claude request artifacts.

Export the manifest command to command.txt and supply the known fixture rate.
For a 10 FPS fixture, run:

    node recording/export.mjs <capture-dir>/frames <capture-dir>/command.txt 10 <output.mp4>
    node recording/validate.mjs <capture-dir>/capture.json <output.mp4>

Put ffprobe on PATH or set FFPROBE_PATH. The validator checks the manifest,
frame paths and timestamps, and the clip's decoded frame count, format,
resolution and duration. Review the overlay, image content, run boundaries
and timing separately. Label timing-adjusted exports as rehearsals while the
mentor ruling is pending. Keep the raw timestamps for later export.

## Scripted Webots demo and recording runbook

Use Webots R2025a, Python with Pillow available to both the launcher and the
Webots controller, Node.js, and FFmpeg with drawtext and libx264 plus ffprobe.
Use separate checkouts for simulator revision 0d19beb and engine revision
7193439. Run the following from the simulator checkout in PowerShell.

```powershell
New-Item -ItemType Directory -Path runs -Force | Out-Null
.\simulation\check_http_fetch.ps1 -EngineServer '<engine-checkout>\engine\server.py' -WebotsExe '<webots-install>\msys64\mingw64\bin\webots.exe'
```

This fixture needs no model key. It runs a scripted red-sphere delivery
through the HTTP engine and records native POV automatically. Check for the
full HTTP fetch and private pass verdict. The fixture also checks the agent's
640×480 JPEG, collision recovery, reset, and finish. Use the manifest path
printed with the native POV frame count.

From the UI and recording checkout, replace the manifest placeholder with
that printed path and run the following. The temporary output directory
keeps the original capture untouched.

```powershell
$manifestPath = '<printed capture.json path>'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$framesPath = Join-Path (Split-Path -Parent $manifestPath) 'frames'
$output = Join-Path $env:TEMP ('rover-rehearsal-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $output | Out-Null
$commandPath = Join-Path $output 'command.txt'
$clipPath = Join-Path $output 'phase2-rehearsal.mp4'
[IO.File]::WriteAllText($commandPath, $manifest.command)
node recording/export.mjs $framesPath $commandPath 31 $clipPath
node recording/validate.mjs $manifestPath $clipPath
```

The reference fixture had 484 frames. At 31 FPS the MP4 runs for 15.61
seconds, compared with 17.89 seconds on the capture wall clock. The fixed rate
is for this rehearsal. Preserve the original simulation and wall timestamps.
Inspect the first two seconds for the exact command and the final frames for
the completed delivery. The fixture verifies capture and export. Hands-free
model delivery and the recording-enabled Claude audit require separate evidence.

## Submission checklist draft

- [ ] Record Paras's confirmation of the recording timing decision in #5.
- [ ] Attach the sanitized Claude recording audit to #4. Verify that request
      images match the 640×480 decision PNGs and private operator artifacts stay local.
- [ ] Confirm the three hands-free fetches and mentor sign-off with Paras in #5.
- [ ] Verify a fresh recursive clone of the integrated revision on a separate
      checkout and rerun the documented scripted smoke.
- [ ] Record each hidden command and use [the packaging runbook](SUBMISSION.md). Validate 10–20 seconds, at
      least 720p, complete rover POV, and the exact command for two seconds.
      Check that the clip contains no team names, logos, or code.
- [ ] Give Paras the revision, reproduction commands, and validated clip list
      for integration approval. Keep credentials, raw requests, private verdicts,
      and original capture clocks in local storage.
