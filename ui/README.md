# Phase 1 operator UI

From the repository root, run `node ui/server.mjs` and open http://127.0.0.1:8090. The monitor reads the engine's `GET /state` and `GET /look` on localhost:8080 through a local proxy. Use the exact loopback URL above. The proxy accepts lifecycle writes from that origin and forwards them to the engine.

Open http://127.0.0.1:8090/?mock to test command entry, Start, status progression, result, and Reset. Mock mode does not contact the engine and shows a camera placeholder. The mock pass tests UI behavior only.

With the real Webots engine running on port 8080, enter a command and click Start. Once the UI reports Locate, launch the agent separately from its checkout with `python -m agent "Fetch the red sphere"` using the same command entered in the UI. Watch the camera and status, then click Reset after Run ended. The engine does not expose the evaluator result yet, so Run ended indicates completion of the engine run only.
