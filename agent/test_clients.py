"""Phase 1 checks with mocked HTTP responses."""

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from agent.claude import ClaudeClient, ClaudeError
from agent.engine import ActionOutcomeUncertain, EngineClient, EngineError


def json_response(payload):
    return "application/json", json.dumps(payload).encode()


class EngineClientTests(unittest.TestCase):
    @patch("agent.engine.request")
    def test_read_contract(self, request):
        client = EngineClient()
        state = dict(state="Blocked", x=1, y=2, heading=90, holding=False, retry_count=1)
        jpeg = b"\xff\xd8mock\xff\xd9"
        request.side_effect = [
            json_response({**state, "object_id": "private"}),
            ("image/jpeg", jpeg),
            HTTPError("/depth", 404, "Missing", None, None),
            json_response({"distance": 10}),
            json_response({"distance": 11}),
        ]
        self.assertEqual(client.state(), state)
        self.assertEqual(client.look(), jpeg)
        self.assertIsNone(client.depth())
        self.assertEqual(client.depth(), 10)
        with self.assertRaises(EngineError):
            client.depth()

    @patch("agent.engine.request")
    def test_action_paths_payloads_and_allowlists(self, request):
        client = EngineClient()
        cases = (
            ("move", {"distance": -0.1}, {"success": False, "collided": True}),
            ("turn", {"degrees": 90}, {"success": True}),
            ("pickup", {}, {"success": True}),
            ("drop", {}, {"success": True, "in_zone": False}),
        )
        for name, args, result in cases:
            with self.subTest(name=name):
                request.return_value = json_response({**result, "object_id": "private"})
                self.assertEqual(client.execute(name, args), result)
                request.assert_called_with(f"http://localhost:8080/{name}", 15, payload=args)

    @patch("agent.engine.request")
    def test_rejects_invalid_actions_before_dispatch(self, request):
        cases = (
            ("move", {"distance": True}),
            ("move", {"distance": float("inf")}),
            ("turn", {"degrees": float("nan")}),
            ("turn", {}),
            ("pickup", {"object_id": "private"}),
            ("reset", {}),
        )
        for name, args in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                EngineClient().execute(name, args)
        request.assert_not_called()

    @patch("agent.engine.request")
    def test_uncertain_actions_are_sanitized_and_never_retried(self, request):
        for response in (TimeoutError("internal details"), json_response({"success": 1})):
            with self.subTest(response=type(response)):
                request.reset_mock()
                request.side_effect = [response]
                with self.assertRaisesRegex(ActionOutcomeUncertain, "^Rover action outcome is uncertain"):
                    EngineClient().execute("pickup", {})
                request.assert_called_once()


@patch.dict(os.environ, {"ANTHROPIC_API_KEY": "mock-key", "CLAUDE_MODEL": "mock-model"})
class ClaudeClientTests(unittest.TestCase):
    @patch("agent.claude.request")
    def test_valid_decision_and_request(self, request):
        usage = {"input_tokens": 20, "output_tokens": 8, "cache_read_input_tokens": 12}
        request.return_value = json_response({
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "name": "move", "input": {"distance": -0.5}}],
            "usage": usage,
        })
        messages = [{"role": "user", "content": "Reverse."}]
        decision = ClaudeClient(timeout=7).decide(messages, system="Choose one action.")
        self.assertEqual((decision.name, decision.args, decision.usage), ("move", {"distance": -0.5}, usage))
        self.assertEqual(request.call_args.args, ("https://api.anthropic.com/v1/messages", 7))
        payload = request.call_args.kwargs["payload"]
        self.assertEqual(payload["model"], "mock-model")
        self.assertEqual(payload["messages"], messages)
        self.assertTrue(payload["tool_choice"]["disable_parallel_tool_use"])
        self.assertEqual([tool["name"] for tool in payload["tools"]], ["move", "turn", "pickup", "drop"])
        for tool in payload["tools"]:
            self.assertTrue(tool["strict"])
            self.assertFalse(tool["input_schema"]["additionalProperties"])
            self.assertEqual(set(tool["input_schema"]["required"]), set(tool["input_schema"]["properties"]))

    @patch("agent.claude.request")
    def test_rejects_missing_multiple_or_invalid_calls(self, request):
        call = {"type": "tool_use", "name": "drop", "input": {}}
        for content in ([], [call, call], [{**call, "input": {"object_id": "private"}}]):
            with self.subTest(content=content):
                request.return_value = json_response({
                    "stop_reason": "tool_use",
                    "content": content,
                    "usage": {"input_tokens": 20, "output_tokens": 8},
                })
                with self.assertRaises(ClaudeError):
                    ClaudeClient().decide([], system="Choose one action.")
