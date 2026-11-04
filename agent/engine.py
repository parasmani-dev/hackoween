"""Client for the rover engine contract."""

import json
from http.client import HTTPException
from urllib.error import HTTPError

from .tools import finite_number, validate_action
from .transport import request


class EngineError(RuntimeError):
    pass


class ActionOutcomeUncertain(EngineError):
    """An action may already have executed."""


_STATES = {"Idle", "Locate", "Go to object", "Pick up", "Carry", "Drop", "Done", "Blocked"}
_RESULT_FIELDS = {
    "move": ("success", "collided"),
    "turn": ("success",),
    "pickup": ("success",),
    "drop": ("success", "in_zone"),
}


class EngineClient:
    def __init__(self, base_url="http://localhost:8080", *, timeout=35):
        if not finite_number(timeout) or timeout <= 0:
            raise ValueError("Engine timeout must be positive and finite.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _read(self, path, *, payload=None, content_type="application/json"):
        actual_type, body = request(self.base_url + path, self.timeout, payload=payload)
        if actual_type != content_type:
            raise ValueError("Unexpected engine response type.")
        if content_type == "image/jpeg":
            if not body.startswith(b"\xff\xd8") or not body.endswith(b"\xff\xd9"):
                raise ValueError("Invalid JPEG response.")
            return body
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError("Expected an engine response object.")
        return result

    def state(self):
        try:
            raw = self._read("/state")
            state = {key: raw[key] for key in ("state", "x", "y", "heading", "holding", "retry_count")}
            if state["state"] not in _STATES or type(state["holding"]) is not bool:
                raise ValueError
            if not all(finite_number(state[key]) for key in ("x", "y", "heading")):
                raise ValueError
            if type(state["retry_count"]) is not int or state["retry_count"] < 0:
                raise ValueError
            return state
        except (OSError, HTTPException, ValueError, KeyError, TypeError):
            raise EngineError("Could not read rover state.") from None

    def look(self):
        try:
            return self._read("/look", content_type="image/jpeg")
        except (OSError, HTTPException, ValueError):
            raise EngineError("Could not read rover camera.") from None

    def depth(self):
        try:
            distance = self._read("/depth")["distance"]
            if distance is not None and not (finite_number(distance) and 0 <= distance <= 10):
                raise ValueError
            return distance
        except HTTPError as error:
            if error.code in (404, 405, 501):
                return None
            raise EngineError("Could not read rover depth.") from None
        except (OSError, HTTPException, ValueError, KeyError):
            raise EngineError("Could not read rover depth.") from None

    def execute(self, name, args):
        validate_action(name, args)
        try:
            raw = self._read("/" + name, payload=args)
            result = {key: raw[key] for key in _RESULT_FIELDS[name]}
            if any(type(value) is not bool for value in result.values()):
                raise ValueError
            return result
        except (OSError, HTTPException, ValueError, KeyError):
            raise ActionOutcomeUncertain("Rover action outcome is uncertain.") from None
