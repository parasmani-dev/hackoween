"""One validated rover decision from the Claude Messages API."""

import json
import os
from dataclasses import dataclass
from http.client import HTTPException

from .config import load_provider_environment
from .observations import OBSERVATION_INSTRUCTION, extract_observation
from .tools import finite_number, tool_definitions, validate_action
from .transport import request


class ClaudeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Decision:
    name: str
    args: dict
    usage: dict[str, int]
    observation: str | None = None


def _claude_tools():
    """Claude strict tools omit numeric bounds unsupported by its schema subset."""
    tools = tool_definitions()
    for tool in tools:
        for field in tool["input_schema"]["properties"].values():
            lower, upper = field.pop("minimum"), field.pop("maximum")
            field["description"] += f" Allowed range: {lower} to {upper}."
    return tools


def _allowed_tool_names(allowed_tools):
    public = frozenset(("move", "turn", "pickup", "drop"))
    if allowed_tools is None:
        return public
    if isinstance(allowed_tools, (str, bytes, dict)):
        raise ValueError("Choose a nonempty set of distinct public rover tool names.")
    try:
        names = tuple(allowed_tools)
    except TypeError:
        raise ValueError("Choose a nonempty set of distinct public rover tool names.") from None
    if (not names or any(not isinstance(name, str) or name not in public for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Choose a nonempty set of distinct public rover tool names.")
    return frozenset(names)


class ClaudeClient:
    def __init__(self, *, timeout=60, max_tokens=2048, audit_request=None):
        load_provider_environment("claude")
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
        self._audit_request = audit_request

    def decide(self, messages, *, system, allowed_tools=None):
        allowed_names = _allowed_tool_names(allowed_tools)
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
            "system": system + OBSERVATION_INSTRUCTION
            + " Return exactly one rover tool call now; do not reply only in text.",
            "tools": [tool for tool in _claude_tools() if tool["name"] in allowed_names],
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        }
        if self._audit_request is not None:
            self._audit_request(payload)
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
            if name not in allowed_names:
                raise ValueError
            usage = {key: response["usage"][key] for key in ("input_tokens", "output_tokens")}
            for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
                if response["usage"].get(key) is not None:
                    usage[key] = response["usage"][key]
            if any(type(value) is not int or value < 0 for value in usage.values()):
                raise ValueError
            observation = extract_observation(
                block["text"] for block in response["content"]
                if block["type"] == "text" and isinstance(block.get("text"), str)
            )
            return Decision(name, args, usage, observation=observation)
        except (OSError, HTTPException, ValueError, KeyError, TypeError):
            raise ClaudeError("Claude did not return a valid rover decision.") from None
