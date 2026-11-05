"""Recovery tool availability through both real provider adapters, offline."""

import base64
import json
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

from agent.runner import run_command
from agent.test_runner import jpeg, state


class RecoveryToolTests(unittest.TestCase):
    def test_rejected_detour_reversal_keeps_safe_scanning_available(self):
        proposals = [
            ("move", {"distance": 0.4}),
            ("turn", {"degrees": 45}),
            ("turn", {"degrees": -30}),
            ("turn", {"degrees": 30}),
            ("move", {"distance": 0.15}),
        ]
        for provider, module in (("claude", "claude"), ("openai", "openai_client")):
            with self.subTest(provider=provider), TemporaryDirectory() as temporary:
                engine = Mock()
                engine.state.side_effect = [
                    state(), state("Blocked"),
                    {**state("Blocked"), "heading": 45},
                    {**state("Blocked"), "heading": 75},
                    {**state("Go to object"), "heading": 75, "x": 0.04, "y": -0.145},
                ]
                engine.execute.side_effect = [
                    {"success": False, "collided": True},
                    {"success": True}, {"success": True},
                    {"success": True, "collided": False},
                ]
                engine.look.side_effect = [jpeg(color) for color in
                                           ("red", "blue", "green", "yellow", "purple")]
                responses = []
                for name, args in proposals:
                    if provider == "claude":
                        body = {
                            "stop_reason": "tool_use",
                            "content": [{"type": "tool_use", "name": name, "input": args}],
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                        }
                    else:
                        body = {
                            "choices": [{"finish_reason": "tool_calls", "message": {
                                "tool_calls": [{"type": "function", "function": {
                                    "name": name, "arguments": json.dumps(args)}}],
                            }}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                        }
                    responses.append(("application/json", json.dumps(body).encode()))

                with patch.dict(os.environ, {
                    "ANTHROPIC_API_KEY": "offline-test-key", "CLAUDE_MODEL": "offline-claude",
                    "OPENAI_API_KEY": "offline-test-key", "OPENAI_MODEL": "offline-openai",
                }), patch(f"agent.{module}.request", side_effect=responses) as request:
                    result = run_command("Fetch the requested object", engine=engine,
                                         provider=provider, output_root=temporary,
                                         max_decisions=5, audit_requests=True)

                self.assertEqual(result.status, "decision_limit")
                self.assertEqual((result.decisions, result.model_requests), (5, 5))
                self.assertEqual(engine.execute.call_args_list,
                                 [call(*proposals[index]) for index in (0, 1, 3, 4)])
                self.assertNotIn(call("turn", {"degrees": -30}), engine.execute.call_args_list)
                rows = [json.loads(line) for line in
                        (result.run_dir / "steps.jsonl").read_text().splitlines()]
                self.assertEqual(rows[2]["result"],
                                 {"outcome": "not_dispatched", "reason": "navigation_guard"})
                events = [json.loads(line) for line in
                          (result.run_dir / "navigation.jsonl").read_text().splitlines()]
                self.assertEqual([event["reason"] for event in events],
                                 ["recovery_requires_translation"])
                payloads = [entry.kwargs["payload"] for entry in request.call_args_list]
                names = {tool["name"] if provider == "claude" else tool["function"]["name"]
                         for tool in payloads[3]["tools"]}
                self.assertEqual(names, {"move", "turn"})

                # The rejected reversal reuses the same image. Continuing the
                # scan dispatches a real turn and refreshes the next observation.
                self.assertEqual(rows[2]["frame_path"], rows[3]["frame_path"])
                self.assertEqual(rows[4]["frame_path"], "0004.png")
                if provider == "claude":
                    encoded = payloads[4]["messages"][0]["content"][1]["source"]["data"]
                else:
                    encoded = payloads[4]["messages"][1]["content"][1]["image_url"]["url"].split(",", 1)[1]
                self.assertEqual(base64.b64decode(encoded),
                                 (result.run_dir / rows[4]["frame_path"]).read_bytes())
                self.assertNotEqual((result.run_dir / rows[3]["frame_path"]).read_bytes(),
                                    (result.run_dir / rows[4]["frame_path"]).read_bytes())
                self.assertEqual(engine.look.call_count, 5)


if __name__ == "__main__":
    unittest.main()
