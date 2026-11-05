# Navigation diagnosis and corrections

Evidence: Haiku attempt `attempt-3cbhsbpw`, 50 requests, private verdict
`fail_not_reached`. Pickup at decision 26 after 10 failed attempts; one collision
at decision 47 during delivery; complete recording. All 50 request frames matched
the saved camera images. This list is the identified issues, not an assertion that
all possible navigation failures have been discovered.

| Issue | Evidence | Correction | Remaining limitation |
|---|---|---|---|
| Drives past a target on the left | Decisions 1–5, 17–18; observation says centered despite lower-left pixels | Require structured current target bounds; controller aligns before forward/pickup proposals | AI bounds may be inaccurate |
| Repeated premature pickups | 10 failed trials; depth .234, .169, .116 before successful .042 | Use visible bounds and clearance to substitute a bounded approach; near clipped target gets pickup before another drive | Single ray does not prove identity |
| Distant depth interpreted as near target | Depth over 3 m while sphere is visibly nearby | Treat a distant ray return as a miss; correct bearing and use small approaches | Side obstacles are not ranged |
| Drives without locating floor zone | Decisions 28–34 and wall-trim descriptions | Require visible floor-zone report before forward delivery; search turn otherwise | Occlusion or false zone report can still prevent delivery |
| Reverse then re-enters blocked route | Collision 47, reverse 48, same-direction drives 49–50 | Retain blocked carrying heading across reverse until measured turn >=15 degrees | A new heading is not proof the whole path is safe |
| Text guidance alone does not constrain actions | Prior prompts already asked for alignment and range checks | Log model proposal and perception alongside one corrected engine action per decision | This is an AI-guided controller, not unmodified model tool execution |

The controller uses AI-reported camera bounds, public rover state and optional
physical range only. No fixture identity, hidden target pose, scripted production
waypoints, or production color detector is added. Both providers use the same
perception schema. Model-only fields are removed before engine dispatch.

## Verification

- Unit regressions cover each correction, invalid bounds, unchanged-pose guards,
  proposal/execution audit, both provider payloads and persistent collision recovery.
- `python -B -m rover_lab.verify_visual_control_offline --engine-port 19336 --output runs/visual-controller-offline-ready`
  uses an explicitly offline pixel instrument to supply bounds and intentionally
  propose pickup on every call. Real Webots: 11 decisions, exactly one successful
  pickup, zero contacts, zero provider calls. This is not live AI acceptance.
- Existing UI/mock-provider delivery verifies transport, lifecycle, capture and
  image audit separately. It does not validate model recognition.
- One additional live Haiku run, maximum 50 requests, is authorized for this phase.
  Its result must be recorded independently; do not infer live success from mocks.

## API schema preflight failure

The first request in `attempt-h6sr31zx` failed before any rover mutation. The new
array schema used minItems=4/maxItems=4, which Anthropic strict tools do not support.
Removed provider-side length constraints and retained exact-four-element local
validation. Added a schema regression. Reference:
https://platform.claude.com/docs/en/build-with-claude/structured-outputs
This aborted preflight request counts against the 50-call test allowance; the
continuation is capped at 49 calls, with both records retained.

## Live controller finding and verified repair

Continuation `attempt-2dsysxiv` ended after 17 decisions with `fail_collision`.
At decision 12, the reported box [0,200,480,480] and depth .064 m warranted pickup
within the 30-degree grasp tolerance. The controller incorrectly applied its
10-degree driving alignment threshold first, producing extra near-target turns.
Repeated failed-turn proposals were also not blocked. These are controller bugs,
not evidence that prompt quality alone is the problem.

Repairs: near-target pickup now has priority within the grasp tolerance; the runner
rejects repeating a failed turn in the same direction. Added both regressions.
Exact saved AI proposal/perception replay in Webots now reaches pickup at decision
12, exactly one pickup attempt, zero contacts, zero model calls. Evidence:
`runs/ai-trace-pickup-fixed/report.json`. Replay is explicitly not a new live test
and does not test delivery. Combined requests so far: one aborted schema request
plus 17 continuation decisions = 18. Further live validation awaits permission to
use the remaining 32 calls as an additional run.

## Final 32-call validation

User authorized the remaining 32 requests. Attempt `attempt-i8lkya2l` on 2ffc33b
used all 32 and ended `fail_not_reached` / `decision_limit`: no successful pickup,
no delivery, no reported move contact or failed turn. All 32 submitted frames
matched saved PNGs, checked private metadata absent, POV complete. Usage:
136611 input / 4591 output tokens. Total phase requests: 1 + 17 + 32 = 50.
No additional live calls are authorized or made.

Further observed failures: AI sometimes reports inaccurate bounds or absent target;
step 28 reported a right-side target but proposed a left turn, and absent-target
forward moves/alternating scans continued. Offline follow-up now corrects turns
away from a visible target when the reported path is clear, requires reacquisition
before blind forward travel with distant/absent depth, and keeps absent-target
search direction consistent. Added exact regressions; 89 tests pass. These fixes
have NOT had another live run. Perception accuracy and delivery remain open;
this is not a successful or perfect autonomous build.

## Screenshot stall: successful pickup followed by delivery-search deadlock

The user's later UI-started `attempt-8o68upm0` picked up at decision 8.
The delivery controller then converted proposed moves into search turns whenever
the floor zone was unrecognized. Direction reversals repeated views; 12 consecutive
turns reached the runner limit. Decisions 21-23 were rejected, and the run ended
`navigation_stalled`. The simulator was not frozen. The matching run was served on
8613; the ambient browser's older 8413 URL was not evidence of this run's origin.

Fix: keep scan direction consistent; after a full scan, permit a maximum 0.15 m
reposition only on a model-reported clear path and outside a known blocked heading.
Keep depth clearance, turn limits, failed-action checks and total decision caps.
Also floor derived motion distances to avoid rounding above the depth guard.
Show an explicit stopped-run message beside the camera feeds, including reset
instructions; a terminal run is never described as still executing.

Offline saved-proposal replay: all 23 decisions execute, successful pickup,
reposition at 17, zero contacts, no navigation stall. Perceptions after trajectory
divergence are recorded test inputs, not current visual truth; delivery remains
unverified. Report: `runs/search-deadlock-replay-fixed/report.json`.
92 offline tests and Node syntax check pass. No new provider calls for this fix.

## Requested 50-call validation of 27e849d

Attempt `attempt-c9jj18sr` stopped after 10 requests, `navigation_stalled` /
`fail_not_reached`. No pickup or delivery, one collision at step 7, three rejected
proposals at 8-10. Complete POV; all 10 outbound camera images match saved PNGs;
checked private metadata absent. Usage 42604 input / 1270 output tokens.

The model repeatedly supplied essentially unchanged target bounds. The controller
converted pickup proposals into approach moves. At depth .150245 m it requested
.1102 m, within the single-ray clearance bound, but contact still occurred: the ray
is not full-chassis clearance. After backoff, pickup proposals were again converted
to .1565 m approaches, which conflict with the collision-direction guard. The
rejection loop terminated. These safety/approach-controller interactions remain
unresolved; this run does not pass any pickup/delivery/no-contact acceptance.
No automatic retry; 40 of the latest 50-call allowance were unused.
