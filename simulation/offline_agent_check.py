"""Scripted decisions through the real agent/HTTP/recording path, no provider calls."""

from dataclasses import asdict
import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent.claude import Decision
from agent.engine import EngineClient
from agent.runner import run_command


def check_agent(server, adapter):
    from simulation import check_http_fetch as launcher

    class ScriptedPolicy:
        def __init__(self):
            self.actions = iter([
                ("turn_to", (-0.96, 0.46)), ("drive_to", (-0.96, 0.46)),
                ("turn_to", (-0.55, 0.65)), ("pickup", None),
                ("turn_to", (-0.65, 0.46)), ("drive_to", (-0.65, 0.46)),
                ("turn_to", (-0.65, -1.65)), ("drive_to", (-0.65, -1.65)),
                ("turn_to", (1.28, -1.65)), ("drive_to", (1.28, -1.65)),
                ("drop", None),
            ])

        def decide(self, messages, *, system):
            pose = json.loads(messages[0]["content"][0]["text"])["state"]
            action, target = next(self.actions)
            if action == "turn_to":
                x, y = target
                degrees = math.remainder(-math.degrees(math.atan2(y - pose["y"], x - pose["x"])) - pose["heading"], 360)
                return Decision("turn", {"degrees": degrees}, {})
            if action == "drive_to":
                x, y = target
                return Decision("move", {"distance": math.hypot(x - pose["x"], y - pose["y"])}, {})
            return Decision(action, {}, {})

    def scripted_child(argv, **kwargs):
        value = lambda option: argv[argv.index(option) + 1]
        result = run_command(argv[3], engine=EngineClient(value("--engine-url")),
                             claude=ScriptedPolicy(), output_root=value("--output-root"),
                             max_decisions=int(value("--max-decisions")))
        assert result.status == "engine_done", result
        return SimpleNamespace(returncode=0, stdout=json.dumps(asdict(result), default=str))

    args = SimpleNamespace(command="Offline scripted red sphere fetch", expected_fixture_key="item_1",
                           record_pov=True, provider="claude", agent_root=Path.cwd(), max_decisions=20,
                           execution_mode="offline_scripted", official_slot=None)
    with patch.object(launcher.subprocess, "run", side_effect=scripted_child), \
         patch("agent.claude.request", side_effect=AssertionError("Model calls forbidden")), \
         patch("agent.openai_client.request", side_effect=AssertionError("Model calls forbidden")):
        record = launcher.run_live(server, adapter, args)
    if (record.get("verdict") or {}).get("outcome") != "pass":
        raise RuntimeError("Scripted fetch did not receive a private pass verdict")
    print("PASS: scripted decisions through agent, HTTP and engine-owned capture; zero model calls")
