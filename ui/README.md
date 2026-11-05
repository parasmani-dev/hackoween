# Phase 1 operator UI

From the repository root, run `node ui/server.mjs` and open http://127.0.0.1:8090. The monitor reads the engine's `GET /state` and `GET /look` on localhost:8080 through a local proxy. Use the exact loopback URL above. The proxy accepts lifecycle writes from that origin and forwards them to the engine.

Open http://127.0.0.1:8090/?mock to test command entry, Start, status progression, result, and Reset. Mock mode does not contact the engine and shows a camera placeholder. The mock pass tests UI behavior only.

With the real Webots engine running on port 8080, enter a command and click Start. Once the UI reports Locate, launch the agent separately from its checkout with `python -m agent "Fetch the red sphere"` using the same command entered in the UI. Watch the camera and status, then click Reset after Run ended. The engine does not expose the evaluator result yet, so Run ended indicates completion of the engine run only.

## Offline Start demo

The **Whole-rover view** button switches the camera panel to an operator-only
overhead JPEG feed when the demo setup publishes one. Set
`ROVER_SPECTATOR_FILE` on the UI server to that feed's absolute file path.
The dashboard reads it directly and keeps the run controls visible in both
views.

With a Webots bridge running, start the real engine using the adapter from
the repository root. Adjust the loopback bridge port to your Webots instance.

```powershell
python -c "from engine.server import create_server; from simulation.adapter import WebotsAdapter; create_server(WebotsAdapter(port=18765)).serve_forever()"
```

In another PowerShell session, enable the fixed red-sphere demo.

```powershell
$env:ROVER_OFFLINE_DEMO = '1'
node ui/server.mjs
```

Open http://127.0.0.1:8090, click Start, and wait for the delivery result.
Click Reset to repeat. Start submits the fixed red-sphere fixture and runs
the scripted driver without any model API calls. Set ROVER_ENGINE_PORT if
the engine uses a different port, and ROVER_UI_PORT for a different UI port.
