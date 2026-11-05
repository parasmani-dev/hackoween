"""Claude action schemas and validation shared with the engine client."""

import math


_ACTIONS = {
    "move": ("Drive forward or reverse.", "distance", "Signed distance in metres."),
    "turn": ("Rotate in place.", "degrees", "Degrees, positive clockwise."),
    "pickup": ("Pick up only within 0.5 m of the rover center and 30 degrees of its heading. "
               "A large camera image does not prove reach. After failure, move or turn to correct "
               "the pose before retrying; repeating at the same pose is rejected.", None, None),
    "drop": ("Release the held object. Approach the requested zone before releasing. "
             "If released outside the zone, pick it up again and correct position before retrying.", None, None),
}


def tool_definitions(available_actions=None, *, visual_memory=False):
    allowed = set(_ACTIONS) if available_actions is None else set(available_actions)
    if not allowed or not allowed <= _ACTIONS.keys():
        raise ValueError("Choose at least one known rover action.")
    return [
        {
            "name": name,
            "description": description,
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {**({field: {"type": "number", "description": units}} if field else {}),
                    **({"visual_observation": {"type": "string", "description":
                        "Camera observation under 400 characters: target pixel bounds [left,top,right,bottom] in 640x480 or absent/occluded; color/shape; visible or clipped; clear route. Centered means midpoint x=256..384. While holding, describe the floor drop zone. Update from this image; do not invent unseen objects or coordinates."}} if visual_memory else {})},
                "required": ([field] if field else []) + (["visual_observation"] if visual_memory else []),
                "additionalProperties": False,
            },
        }
        for name, (description, field, units) in _ACTIONS.items()
        if name in allowed
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


def extract_visual_observation(args):
    """Separate model-authored memory from the unchanged engine action payload."""
    if not isinstance(args, dict):
        raise ValueError("Invalid action fields.")
    args = dict(args)
    observation = args.pop("visual_observation", "")
    if not isinstance(observation, str) or len(observation) > 1000:
        raise ValueError("Invalid visual observation.")
    return args, observation[:400]
