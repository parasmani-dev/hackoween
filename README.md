# Rover Fetch

Software-only hackathon workspace. Working title pending team naming.

## Current status

Embodify source is checked out in `upstream/embodify` at commit `6be154471801f4cea47f3df1f46b000476b3eeb6`. Its original Git history, Apache-2.0 licence, NOTICE, and third-party notices are retained. This is a foundation checkout; the rover simulation and Claude HTTP agent still need implementation.

The existing backend contract is arm-oriented (end-effector poses, grippers, and arm metadata). Reuse episode logging, budget patterns, and replay where compatible. Implement rover movement through a dedicated interface rather than pretending a rover is a robot arm. Keep evaluation truth inside the simulator.

## Run the baseline check

From the workspace in PowerShell, with Python and NumPy installed

```powershell
Push-Location upstream/embodify
python -m embodify_mcp.smoke --output-root ../../runs/baseline
Pop-Location
```

This exercises the diagnostic backend, MCP transport, observations, episode reset, and saved summaries. It does not validate rover physics, GPU rendering, or Claude.

## Four-person implementation split

| Owner | Deliverable | Acceptance check |
| --- | --- | --- |
| Paras | Webots room, rover, front camera, six shapes, drop zone, collision and snap pickup | Scripted moves can pick and drop an object with real collision handling |
| Vernit | Local HTTP API and state machine, serialized actions, retries, success evaluator | Required endpoints work and expose only permitted fields |
| Archit | Python Claude API loop, tool schemas, image transmission, history and step budget | One tool call per decision, correct visual target selection, no hidden object data |
| Ayush | Command interface, recording, video export and submission tooling | Reproducible runs, complete JSONL/PNG artifacts, compliant MP4 clips |

## Shared interface to implement first

- GET /state returns state name, robot x/y, heading, holding boolean and retry count.
- GET /look returns 640x480 JPEG from the front camera.
- Optional GET /depth returns a forward single-ray distance, capped at 10 metres, or null.
- POST /move accepts distance in metres, including negative values.
- POST /turn accepts degrees, positive clockwise.
- POST /pickup returns success/failure without object identity.
- POST /drop releases the held object and reports zone inclusion.

Keep object identities, colours, shapes, locations, counts, masks and zone coordinates out of every model-visible payload. Spectator views and evaluator data must never enter Claude context. Treat simulator exceptions as internal diagnostics and return sanitized action failures.

Paras owns phased integration, shared configuration, and merges. Ayush supplies UI/recording deliverables for those gates.

## Integration milestones

1. Agree on the interface and run a scripted fetch through HTTP.
2. Add Claude, the initial image, and fresh images after movement or turning. Reuse the latest frame for pickup/drop decisions without consecutive fresh images.
3. Implement collision backoff of 0.1 m, Blocked recovery, three-retry failure, and a 60-decision cap. Freeze simulation while waiting on Claude.
4. Pass three different commands hands-free and obtain mentor sign-off, targeting hour 14.
5. Record the three hidden commands at hour 18 and upload by hour 20. Validate 10–20 second clips, 720p or above, command overlay for two seconds, and no logos or code. Resolve timing/export interpretation with mentors before hidden runs.

## Machine setup

Each laptop should run a complete independent instance. Install Webots, Git, and an isolated Python environment. Use the organizer-provided Claude API key through an environment variable. Add the Membrane key only after sign-off. Start with the RTX 5060 for demos and RTX 4050 for parallel development, then select using actual tests. RAM, drivers, and rendering performance remain unverified.

## Attribution and publication

Use original team branding for the interface. Preserve upstream licence and NOTICE files and document copied or modified upstream components. Disclose external code according to organizer rules. Before creating the public submission repository, decide how to package the nested upstream checkout so a fresh clone includes its pinned source.
