# Rover contract v1

## Process boundary

The simulator owns all objects and physics. Vernit exposes HTTP on localhost:8080. Archit's agent sees only the allowlisted state, robot camera image, optional single-ray depth, and action outcomes. Evaluator truth is local to the engine. Object metadata must never enter prompts, tool schemas, returned exceptions, or model-visible filenames.

Paras provides a Python simulator adapter in simulation/adapter.py with state(), look(), depth(), move(distance), turn(degrees), pickup(), drop(), and close(). A simulator bridge is acceptable if Webots requires a separate process. Vernit and Paras must agree transport details in their issues before implementation. State returns a dictionary, look returns JPEG bytes, depth returns float or None, and actions return dictionaries matching the HTTP contract. Methods complete only after their action completes.

## HTTP payloads

- GET /state returns {state, x, y, heading, holding, retry_count}. State is Idle, Locate, Go to object, Pick up, Carry, Drop, Done, or Blocked. holding is boolean.
- GET /look returns image/jpeg, 640x480, 90-degree horizontal FOV, front camera 0.5 m high tilted 10 degrees down.
- GET /depth returns {distance}, metres up to 10 or null.
- POST /move accepts {distance}, metres, negative reverses, speed 1 m/s. Returns {success, collided}.
- POST /turn accepts {degrees}, positive clockwise, speed 90 degrees/s. Returns {success}.
- POST /pickup accepts {}, returns {success}. Reach 0.5 m, heading tolerance 30 degrees. No identity returned.
- POST /drop accepts {}, returns {success, in_zone}.

Heading zero is +x. Serialize mutations. Reject nonfinite numbers and malformed requests before mutation. A collision stops motion, backs off 0.1 m, and enters Blocked. After three failed recovery retries terminate. Agree retry accounting before integration. Evaluation can distinguish pass, fail_wrong_object, fail_not_reached, fail_collision without exposing the selected object's identity to Claude.

Lifecycle start/reset and evaluator endpoints belong to the operator interface and must be designed by Vernit with Paras. They are never Claude tools. A command has exactly one run at a time. Freeze physics while Claude thinks. Advance the world during commanded motion, with deterministic pickup/drop attachment updates.

## Agent and artifacts

Use the supplied Claude model through an environment variable. One tool call per decision, initial camera image, fresh look after every move or turn, latest frame reused for pickup/drop. Never send consecutive fresh images without move/turn. Use latest image and compact textual history. Soft target 25 decisions, hard limit 60. Bound network waits and handle uncertain action outcomes without blindly repeating a mutation.

Write runs/<run-id>/steps.jsonl with step, timestamp, tool, args, result, frame_path and neutral numbered PNG frames. Run IDs must avoid concurrent overwrites. No credentials in logs. Simulator errors remain internal. Recording is robot POV at 720p or above, 10–20 seconds, command overlay first two seconds. Keep spectator imagery separate from model input. Confirm video time handling with mentors before hidden tests.

## First acceptance milestone

Scripted HTTP fetch with collisions, then three different Claude-directed fetches hands-free. Verify absence of metadata leakage by inspecting actual requests sent to Claude. Diagnostic Embodify success alone does not satisfy this milestone.
