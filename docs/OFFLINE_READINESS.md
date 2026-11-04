# Offline readiness and remaining acceptance evidence

This register follows Archit's technical review in issue #5. Passing offline tests
is not a claim of live autonomy, exact physical speeds, scene FPS or submission approval.
The user authorized Paras to consolidate the remaining offline work across owners.

## Reproduce before every demo or integration

From a clean checkout, install Python dependencies from agent/requirements.txt,
use Node.js, Webots R2025a and a complete FFmpeg/ffprobe distribution, then run:

```powershell
./integration/offline_preflight.ps1 -WebotsExe C:/path/to/webots.exe -FfmpegBin C:/path/to/ffmpeg/bin
```

The preflight removes provider keys from its child environment and restores them
on exit, accepts no model command or official slot, and invokes the scripted-agent
check with both provider transports blocked. It fails immediately on a failed stage.
It pins FFMPEG_PATH and FFPROBE_PATH to the validated directory for child tests
and restores previous overrides on exit. The HTTP fixture always disables the
dashboard scripted driver, regardless of the parent shell setting. The scripted
agent stage requires a private pass verdict; Done alone is not success. The live
launcher continues preserving scored failures and their captures.
It runs 47 Python regressions, actual FFmpeg packaging, physical wheel/stall checks,
and full Webots HTTP/UI fetch, repeated resets, collision recovery, calibrated
motion, frozen non-motion operations and scripted-agent native capture.
Logs are local under runs/. Keep logs private; link a sanitized result in #5.
GitHub offline CI runs Python and syntax checks only, with no repository secrets.
A green CI job does not replace the local Webots/FFmpeg run. Repository branch
protection must require that job if the team wants GitHub to block a failing merge;
this PR does not change repository settings.

## Evidence and remaining gates

| Area | Existing evidence / regression | Remaining requirement | Owner |
|---|---|---|---|
| Room, camera, HTTP and reset | [merged-main rehearsal](https://github.com/parasmani-dev/hackoween/issues/5#issuecomment-6642249370); existing HTTP regressions | Repeat preflight on chosen demo laptop; record its revision and hardware | Paras |
| No-contact two-second stall | simulation.test_motor; real motor_demo wheel-fault fixture in both directions, fails outside one 32 ms physics step | Retain latest preflight log | Paras / Vernit |
| Recovery and private verdicts | engine.test_run_control and actual HTTP collision/retry scenarios | Live recovery evidence is still separate | Vernit / Paras |
| Nominal speeds | measure_timing asserts move and turn simulation durations within 150 ms, distance within 15 mm and angle within 3 degrees | These are regression tolerances, not organizer-approved deviations; report actual values | Paras / Vernit |
| Scene/render FPS | No dedicated graphical render measurement yet | Measure on the selected laptop with the final scene and settings; include method, interval, load and actual result | Paras |
| Agent image reuse, single tools and rejected decisions | agent.test_runner and agent.test_clients; docs/CONTRACT.md | Current contract reuses frames after pickup/drop/rejection. Obtain authoritative interpretation before changing it; real outbound payload audit remains live work | Archit / Paras |
| Recording and failures | simulation.test_capture, engine.test_run_control, real recording/package-submission.test.mjs | Agree official playback clock and inference-pause handling; retain both clocks | Ayush / Paras |
| Usage and caching | Decision ceiling and token accounting are tested | Assign ledger operator before live runs; count failed runs, alert at $30, target $40 and stop development at 120 runs. These are guidance, not a dollar-limit enforcement feature. Cache configuration/savings are unverified | Archit; Paras approves live window/cap |
| Live acceptance | None established by scripted tests | Three distinct hands-free Claude commands, real request audit and recovery; explicit authorization required | Archit / Paras |
| Submission | Once-only private slot ledger and packaging regressions exist | Official commands, required event gates, public repository decision, approved clips, upload/feedback receipts and final package | Paras / Ayush / Archit |

## Keep performance measurements separate

- Browser preview (PR #19): at most 20 FPS; operator display only; merged before this integration. Do not count it as agent camera or native capture.
- Capture wall throughput: frames produced per wall second during motion, including
  capture overhead. It does not measure graphical Webots viewport refresh.
- Simulation cadence: fixed physics step; 32 ms means 31.25 steps per simulated
  second, not proof of 30 rendered frames per wall second.
- Scene/render FPS: still needs a documented measurement on the intended hardware.

Do not close external/live gates from an offline PASS. No organizer interpretation,
API access, cache saving or hardware performance may be inferred from a green test.
Keep the preview PR separate and rerun the preflight after integration.
