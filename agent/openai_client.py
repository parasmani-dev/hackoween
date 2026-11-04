"""OpenAI Chat Completions adapter for the rover decision loop."""

import json
import os
from http.client import HTTPException

from .claude import Decision
from .tools import tool_definitions, validate_action
from .transport import request


class OpenAIError(RuntimeError):
    pass


class OpenAIClient:
    def __init__(self, *, audit_request=None):
        self._api_key = os.environ.get("OPENAI_API_KEY")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
        if not self._api_key or not self.model:
            raise OpenAIError("Set OPENAI_API_KEY and select a valid OPENAI_MODEL.")
        self._audit_request = audit_request

    def decide(self, messages, *, system):
        converted = [{"role": "system", "content": system}]
        for message in messages:
            parts = []
            for block in message["content"]:
                if block["type"] == "text":
                    parts.append(block)
                else:
                    source = block["source"]
                    url = f'data:{source["media_type"]};base64,{source["data"]}'
                    parts.append({"type": "image_url", "image_url": {"url": url}})
            converted.append({"role": message["role"], "content": parts})

        tools = []
        for definition in tool_definitions():
            definition["parameters"] = definition.pop("input_schema")
            tools.append({"type": "function", "function": definition})
        payload = {
            "model": self.model, "messages": converted, "tools": tools,
            "tool_choice": "required", "parallel_tool_calls": False,
            "max_completion_tokens": 2048, "store": False,
        }
        if self._audit_request is not None:
            self._audit_request(payload)
        try:
            content_type, body = request(
                "https://api.openai.com/v1/chat/completions", 60, payload=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            if content_type != "application/json":
                raise ValueError
            response = json.loads(body)
            choice, = response["choices"]
            call, = choice["message"]["tool_calls"]
            if choice["finish_reason"] != "tool_calls" or call["type"] != "function":
                raise ValueError
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"])
            validate_action(name, args)
            raw_usage = response["usage"]
            cached = (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            total, output = raw_usage["prompt_tokens"], raw_usage["completion_tokens"]
            if any(type(value) is not int or value < 0 for value in (total, cached, output)):
                raise ValueError
            if cached > total:
                raise ValueError
            return Decision(name, args, {
                "input_tokens": total - cached,
                "cache_read_input_tokens": cached,
                "output_tokens": output,
            })
        except (OSError, HTTPException, ValueError, KeyError, TypeError, AttributeError):
            raise OpenAIError("OpenAI did not return a valid rover decision.") from None
