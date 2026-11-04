"""Claude action schemas and validation shared with the engine client."""

import math


_ACTIONS = {
    "move": ("Drive forward or reverse.", "distance", "Signed distance in metres."),
    "turn": ("Rotate in place.", "degrees", "Degrees, positive clockwise."),
    "pickup": ("Pick up the object within reach and aligned with the rover.", None, None),
    "drop": ("Release the held object.", None, None),
}


def tool_definitions():
    return [
        {
            "name": name,
            "description": description,
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {field: {"type": "number", "description": units}} if field else {},
                "required": [field] if field else [],
                "additionalProperties": False,
            },
        }
        for name, (description, field, units) in _ACTIONS.items()
    ]


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_action(name, args):
    if not isinstance(name, str) or name not in _ACTIONS:
        raise ValueError("Unknown rover action.")
    field = _ACTIONS[name][1]
    if not isinstance(args, dict) or args.keys() != ({field} if field else set()):
        raise ValueError("Invalid rover action fields.")
    if field and not finite_number(args[field]):
        raise ValueError("Rover action requires a finite number.")
