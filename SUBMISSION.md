# Rover Fetch submission

Rover Fetch combines a Webots simulation, a local HTTP engine, a Python vision
agent, and an operator dashboard. The engine owns physics, collisions, pickup,
drop, and private delivery evaluation.

## How Claude identifies the requested object

The Python agent sends Claude the user's typed command, the latest 640 x 480
front-camera image, the allowlisted rover state, and compact recent action
history. Claude uses the visible colour, shape, size, and spatial relationships
in the camera pixels to identify the requested object and choose a movement,
turn, pickup, or drop action. Python validates the proposed action before
dispatching it to the engine. A move or turn is followed by a fresh camera
image for the next decision. Pickup and drop reuse the latest frame. Action
results inform subsequent decisions and collision recovery. Object identities,
fixture coordinates, and private evaluator verdicts stay outside Claude's
context. The agent saves a JSONL tool-call log and numbered decision PNGs so
judges can inspect the images used for each decision.

## Project and reproduction

- [Engine and simulator contract](docs/CONTRACT.md)
- [Python agent setup and decision-log format](agent/README.md)
- [Webots simulator setup](simulation/README.md)
- [Rover Lab setup and offline scenario verification](rover_lab/README.md)
- [Integrated demo runbook](docs/DEMO_RUNBOOK.md)
- [Clip export, validation, and submission packaging](recording/SUBMISSION.md)

Rover Lab's offline demonstrations use scripted decisions with real Webots
physics and cameras. Official evidence must identify the Claude-directed runs
used for the submission.

## Official run evidence and live demo

The three official hidden-command clips, their original tool-call logs and
referenced decision images, and the confirmed live demo booking are pending
the official runs and organizer confirmation. Preserve failed official attempts
alongside successful ones. The submission packager binds clips and decision
evidence to their launcher attempts and writes the repository link,
visual-identification paragraph, and demo booking into `submission.json`.

The submission repository must be publicly accessible when delivered to the
organizers. Repository publication and final evidence links are completed by
the team before submission.
