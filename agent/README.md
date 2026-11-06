# Rover agent

Motion tools accept moves from -10 to 10 metres and turns from -360 to 360 degrees per action. The tool schemas and client validation use the simulator bridge limits. The HTTP engine rejects requests outside those limits before dispatch, preserving the active run.
Claude's strict schema subset rejects numeric minimum/maximum keywords, so its
wire schemas describe the ranges in text. Local validation still enforces both
limits before dispatch; the OpenAI schemas retain the numeric constraints.
The Claude adapter uses automatic tool choice for Sonnet 5.5 compatibility and
explicitly requests one call. Text-only, parallel, or invalid calls are rejected
before rover dispatch. Requests have a 60-second timeout and 2,048-token output cap.

Both providers use the same navigation guards. The runner rejects zero-distance
moves, remembers the last four collided robot approaches across compact-history
eviction, and rejects re-entering a nearby blocked direction until the rover takes
a detour. After a failed pickup, another pickup requires a new camera viewpoint.
During collision recovery, a chosen side turn cannot immediately be reversed:
the rover must first create over 0.3 m of lateral clearance from the failed
approach, or keep scanning for a safe route. Forward drift alone does not clear
this recovery state. This clearance uses the robot footprint, not target identity.
Search memory tracks observed heading sectors in up to sixteen areas using public
rover poses and advises changing viewpoint after a full scan. This memory belongs
to one command and is discarded on the next run. It contains no fixture locations
or evaluator truth. Rejections remain in `steps.jsonl`; `navigation.jsonl` records
the specific guard reason. These guards constrain actions; they do not guarantee
visual recognition, a collision-free route, or delivery.
The prompt supplies the rover's fixed camera calibration (0.22 m ahead of its
center, 0.5 m high, 10 degrees down, 90-degree horizontal FOV). Pickup distance is
measured from the rover center; near-target guidance uses 0.02 m probes.
The middle third of the camera is sufficient pickup alignment within the
30-degree grasp tolerance. The prompt prioritizes a pickup trial over further
fine alignment once the base is clipped, and asks for a broad-face approach to
cubes rather than driving into a corner. Recovery retains both movement and
turning so a blocked side route can be scanned further; the directional guards
still reject reversing a detour or driving back into known contact.
After a failed pickup on the same approach, model-proposed forward probes are limited to 0.02 m
and a pickup check is required before another forward probe. Pose change is
checked so no-op turns or new filenames cannot unlock duplicate pickups. Turns
of 2 degrees or less, pickup while holding, and drop without holding are rejected.
Collision checks cover the proposed motion segment. Completing a real lateral
detour retires nearby co-oriented bans while retaining orthogonal and distant
collision evidence.

Each provider can return a short `OBSERVATION:` line alongside its single tool
call. Only that factual text line is retained; thinking blocks and subsequent
reasoning are excluded. `observations.jsonl` records the note's numbered frame
and public rover pose. The next request includes the initial observation and up
to six recent notes, labeled as fallible camera observations. No extra perception
call is made per decision. Notes containing reserved fixture/evaluator or
credential markers are discarded. Visual memory resets with each command.

The CLI and local website enable a hybrid approach profile: Claude/OpenAI handles
visual navigation; a bounded local controller tracks a clipped target and runs
approach probes with pickup feedback. Acquisition requires explicit clipped
base evidence and a confident, centered foreground component. The controller
checks visual continuity after each advance and returns control to the model on
tracking loss, ambiguity, contact, a turn, or a 12-probe batch limit. It uses only
camera pixels and public action feedback, with no target positions or evaluator
data. Low-saturation or untrackable objects remain under model control.

When an earlier frame explicitly shows actual floor contact, the controller can
project that point using camera calibration and track it through public rover
motion. With continuous visual association, it uses advances up to 10 cm while
farther away, then returns to 2 cm final probes. Pixel/pose uncertainty, body
clearance, and expected image growth bound each advance. A pickup check follows
every move. Without a trustworthy floor anchor it retains the 2 cm behavior.

For an explicitly requested green zone, this hybrid Rover Lab profile also
remembers the camera-observed marked floor polygon through occlusion. It uses
the Rover Lab 0.60 m attachment calibration and requires the release point to be
20 cm inside that observed region. An early drop proposal becomes a short
alignment or approach correction, subject to the normal motion guards.
`adjustments.jsonl` preserves the original proposal and applied action; its
provider invocation still counts in `model_requests`. This floor-zone helper
does not identify other zone colors or certify arbitrary obstacle routes.

The hybrid profile also projects visible floor edges of tall neutral or cool
panels into a bounded obstacle memory. The edges remain relevant when a close
panel leaves the camera view. A swept body check redirects an unsafe advance
into a consistent side detour, with short moves until public odometry confirms
both forward and lateral clearance. Turns and forward motion account for the
Rover Lab carried attachment as well. These checks cover observed panels;
other objects and unseen obstacles still require camera-guided navigation.
After contact, a recovery scan continues far enough outside the blocked heading
before side travel, rather than repeatedly proposing forbidden approaches.
All corrections retain their original and applied actions in `adjustments.jsonl`.

Cone approaches retain a verified floor anchor through small safe alignment
turns, reassociating it with the refreshed camera before further local motion.
Earlier partial green-floor views can supply a return search direction before
pickup. Such hints cannot authorize a drop: the full marked floor-region check
still applies when the rover arrives.

All local actions still count toward the 60-decision limit. `model_requests`
counts provider invocations separately. `decision_sources.jsonl` identifies
`model`, `local_approach_probe`, or `local_pickup_check` for each step and maps
provider request indices to steps. This profile is not an entirely model-decided
run. Use CLI `--model-pickup-checks` for model-only decisions. Python callers keep
the previous behavior by default; opt in with `local_pickup_checks=True`.

Install the dependencies with `python -m pip install -r agent/requirements.txt`.
Set `ANTHROPIC_API_KEY` and `CLAUDE_MODEL` in the environment or in the repository-root
`.env` file. Both provider clients load their own key/model from that file,
independent of the working directory. Existing environment values take precedence;
other `.env` variables are ignored and credential values are never interpolated.
Keep `.env` ignored by Git and never paste keys into commands or issue comments. After the
operator starts an engine run, invoke the agent from the repository root.

```powershell
python -m agent "Fetch the red sphere and drop it in the green zone"
```

The agent supports Claude and OpenAI. For OpenAI, set `OPENAI_API_KEY` and
optionally set `OPENAI_MODEL` to override the default `gpt-5-mini`. The
project key needs write access to `POST /v1/chat/completions` and the project
needs access to the selected model.

```powershell
python -m agent --provider openai "Fetch the red sphere and drop it in the green zone"
```

The CLI prints a JSON result with the stop reason, decision count, token usage,
and run directory. Python callers can use `agent.runner.run_command` directly.
`engine_done` means the engine reached its terminal state. The operator
evaluator reports the task outcome separately. The runner treats `Done` or
`Blocked` with `retry_count >= 3` as exhausted recovery.

The runner writes one `steps.jsonl` row per decision under a unique directory
in `runs/`. Its UTC timestamp is the dispatch time for dispatched actions or
the time the runner recorded a rejection or provider error. A
`not_dispatched` result means the engine received no action for that row.
A provider error without a validated proposal uses a null tool and empty args.
`frame_path` points to the numbered PNG used for the decision, relative to the
run directory. The runner takes a fresh look after a dispatched move or turn.
After pickup, drop, or a rejected proposal it reuses the most recent frame.
Repeated paths record reuse of that observation.

For an integration audit, add `--audit-requests`. The run directory then
contains `claude_requests.jsonl` or `openai_requests.jsonl` with each outbound request body recorded just
before the send attempt. The audit includes the command and camera pixels in
base64. Keep it local and review the bodies before sharing findings. HTTP
headers and API credentials are excluded.

Use `decision_sources.jsonl` to match audit request N to its corresponding step
in `steps.jsonl`, including rejected proposals. Local controller steps have no
provider request. For older model-only artifacts without a source map, audit
line N corresponds to decision N.
Decode the image and compare it to the referenced PNG. Check that the command,
allowlisted rover state and action history, and four public tools are the only
run context. Keep fixture identity, evaluator results, and native POV out of
requests. A provider request can still consume usage when its action is rejected.

For offline budget planning, maintain a local ledger with one row per run,
including failed runs. Record provider, model, uncached input, cache writes,
cache reads, output tokens, and estimated cost using the approved model rates.
Calculate total estimated spend and flag totals at or above $30. Compare planned
runs with the remaining slots in the 120-run reserve and flag a plan that would
leave fewer slots than needed. Treat estimates and reserve warnings as operator
guidance. The runner does not enforce a monetary cap. Keep cache token classes
separate where the provider reports them, and verify cache use with usage
counters before assuming savings.

Run the client and loop tests with mocked services using
`python -m unittest discover -s agent -v`.
