# Local submission packaging

Use the merged demo runbook to record each approved hidden command. Keep its
native capture and agent run in separate directories. Preserve both original
capture clocks. Keep the launcher attempt directory with its operator result and
single agent run. Export from the manifest printed by the launcher:

```powershell
node recording/export.mjs --capture '<capture.json>' <fps> '<clip.mp4>'
node recording/validate.mjs '<capture.json>' '<clip.mp4>'
```

Use the capture's cadence to choose the fixed export rate and verify that the
result fits the 10–20 second requirement. Keep the adjacent
`clip.mp4.export.json` with the clip. The packager checks this receipt against
the capture selected by the operator result and requires a scored terminal verdict
(`pass`, `fail_wrong_object`, `fail_not_reached`, or `fail_collision`),
`pov_status: "complete"`, and a `pov_capture_id` that matches the manifest's
`run_id`. Older attempts missing these fields require operator review. Run each
official hidden command once and retain its original evidence, including failures.

Save a JSON configuration outside the repo with the fields below. Paths are
relative to the configuration file unless absolute.

```json
{
  "repository": "https://github.com/parasmani-dev/hackoween",
  "visual_identification": "Replace with the final visual-identification paragraph.",
  "demo_booking": "Replace with the confirmed demo booking.",
  "runs": [
    { "attempt": "attempt-one", "clip": "one/clip.mp4", "reviewed": false },
    { "attempt": "attempt-two", "clip": "two/clip.mp4", "reviewed": false },
    { "attempt": "attempt-three", "clip": "three/clip.mp4", "reviewed": false }
  ]
}
```

For each entry, check that the clip and agent log belong to the same accepted
run. Play the full clip and check the exact command during the first two seconds,
robot POV throughout, the complete ending, and the absence of team names, logos,
or code. Review the tool-call log for private material. Set `reviewed` to true
after these checks. Use ffprobe on PATH or set `FFPROBE_PATH` to its executable.

```powershell
node recording/package-submission.mjs '<config.json>' '<new local output directory>'
```

The output contains three numbered MP4s, `submission.json`, and one directory
per run with `steps.jsonl` and its referenced 640×480 decision PNGs. Keep each
log with its directory so `frame_path` stays resolvable. These names are for
local handoff. Apply any organizer naming rules before upload. Keep receipts,
operator results, raw native captures, request audits and credentials outside
the package. Paras coordinates the final upload.
