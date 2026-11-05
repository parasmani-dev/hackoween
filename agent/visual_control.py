"""Conservative corrections from model-reported camera bounds and public depth.

No fixture lookup, image color detector, or private world coordinates. The model
still identifies the target and assesses free space; its estimates can be wrong.
Each correction dispatches one ordinary action and is recorded beside the proposal.
"""
import math
from dataclasses import replace


def correct_action(decision, *, holding, depth, disabled=()):
    view = decision.target_view
    if view is None:  # Existing injected policies remain compatible.
        return decision, None
    box = view['box']
    forward = decision.name == 'move' and decision.args['distance'] > 0
    reaching = decision.name in ('pickup', 'drop')

    def use(name, args, reason):
        if name in disabled:
            return decision, None
        return replace(decision, name=name, args=args), reason

    if holding and (box is None or not view['floor_zone']):
        if forward or decision.name == 'drop':
            return use('turn', {'degrees': 45}, 'search_for_visible_floor_zone')
        return decision, None
    if box is None:
        return decision, None
    center = (box[0] + box[2]) / 2
    bearing = math.degrees(math.atan((center - 320) / 320))
    # Pickup tolerates 30 degrees. Near-field boxes are clipped and their
    # midpoint is less reliable; never demand driving alignment before grasp.
    if (not holding and box[3] >= 475 and depth is not None and depth <= .08
            and abs(bearing) <= 30 and (forward or decision.name == 'pickup')):
        return use('pickup', {}, 'near_target_pickup_before_contact')
    if abs(bearing) > 10 and (forward or reaching):
        return use('turn', {'degrees': round(max(-25, min(25, bearing)), 2)},
                   'align_to_reported_target')
    if not view['path_clear']:
        if forward:
            return use('turn', {'degrees': 25 if center < 320 else -25},
                       'inspect_clearance_before_advancing')
        return decision, None
    if holding:
        # Geometry for the ground plane is not reliable when a zone is occluded.
        # Let the model judge arrival; prevent long blind drives with carried load.
        if forward and decision.args['distance'] > .3:
            return use('move', {'distance': .3}, 'bounded_carry_step')
        return decision, None
    if decision.name not in ('pickup', 'move') or (decision.name == 'move' and not forward):
        return decision, None
    clipped = box[3] >= 475
    if clipped and depth is not None and depth <= .08:
        return use('pickup', {}, 'near_target_pickup_before_contact')
    if decision.name == 'pickup':
        # A fully visible ground object cannot be within reach for this camera.
        # A clipped object with a distant ray return is NOT a near depth reading.
        step = .2 if not clipped else .05
        if clipped and depth is not None and depth < .6:
            step = min(.15, max(0, depth - .055))
        if depth is not None:
            step = min(step, max(0, depth - .04))
        if step > .005:
            return use('move', {'distance': round(step, 4)}, 'approach_before_premature_pickup')
    if forward and depth is not None and decision.args['distance'] > max(0, depth - .04):
        step = max(0, depth - .045)
        if step > .005:
            return use('move', {'distance': round(min(.2, step), 4)}, 'range_bounded_approach')
    return decision, None
