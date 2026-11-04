"""One validated rover decision from the Claude Messages API."""

import json
import os
from dataclasses import dataclass
from http.client import HTTPException

from .tools import finite_number, tool_definitions, validate_action
from .transport import request


class ClaudeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Decision:
    name: str
    args: dict
    usage: dict[str, int]


class ClaudeClient:
    def __init__(self, *, timeout=30, max_tokens=512):
        self._api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.model = os.environ.get("CLAUDE_MODEL")
        if not self._api_key or not self.model:
            raise ClaudeError("Set ANTHROPIC_API_KEY and CLAUDE_MODEL.")
        if not finite_number(timeout) or timeout <= 0:
            raise ValueError("Claude timeout must be positive and finite.")
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("Claude max_tokens must be a positive integer.")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def decide(self, messages, *, system):
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "system": system,
            "tools": tool_definitions(),
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        }
        try:
            content_type, body = request(
                "https://api.anthropic.com/v1/messages",
                self.timeout,
                payload=payload,
                headers={"x-api-key": self._api_key, "anthropic-version": "2023-06-01"},
            )
            if content_type != "application/json":
                raise ValueError
            response = json.loads(body)
            if response["stop_reason"] != "tool_use" or not isinstance(response["content"], list):
                raise ValueError
            calls = [block for block in response["content"] if block["type"] == "tool_use"]
            if len(calls) != 1:
                raise ValueError
            name, args = calls[0]["name"], calls[0]["input"]
            validate_action(name, args)
            usage = {key: response["usage"][key] for key in ("input_tokens", "output_tokens")}
            if any(type(value) is not int or value < 0 for value in usage.values()):
                raise ValueError
            return Decision(name, args, usage)
        except (OSError, HTTPException, ValueError, KeyError, TypeError):
            raise ClaudeError("Claude did not return a valid rover decision.") from None
