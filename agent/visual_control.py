"""Conservative corrections from model-reported camera bounds and public depth.

No fixture lookup, image color detector, or private world coordinates. The model
still identifies the target and assesses free space; its estimates can be wrong.
Each correction dispatches one ordinary action and is recorded beside the proposal.
"""
import math
from dataclasses import replace


def correct_action(decision, *, holding, depth, disabled=(), history=(), allow_exploration=True, recovering=False):
    # Recovery is a higher-priority state than visual approach. Never turn a
    # non-moving pickup trial or an escape turn into another forward approach.
    if recovering:
        if decision.name == 'move':
            collision = next((a for a in reversed(history)
                              if a['tool'] == 'move' and a['result'].get('collided')), None)
            if collision and decision.args['distance'] * collision['args']['distance'] > 0:
                retreat = -.15 if collision['args']['distance'] > 0 else .15
                return replace(decision, name='move', args={'distance': retreat}), 'collision_recovery_before_approach'
        return decision, None
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
        # A full rotation can leave the floor zone occluded by the carried load.
        # Repeating the same view indefinitely conflicts with the runner's turn cap.
        sweep = 0.0
        direction = None
        for action in reversed(history):
            if action['tool'] != 'turn' or not action['result'].get('success'):
                break
            angle = action['args']['degrees']
            if not angle:
                continue
            sign = 1 if angle > 0 else -1
            if direction is None:
                direction = sign
            if sign != direction:
                break
            sweep += abs(angle)
        if (sweep >= 350 and view['path_clear'] and allow_exploration
                and (forward or decision.name in ('turn', 'drop'))):
            distance = min(.15, max(0, depth - .04)) if depth is not None else .15
            if distance >= .02:
                return use('move', {'distance': math.floor(distance * 10000) / 10000},
                           'reposition_after_full_zone_scan')
        if forward or decision.name in ('turn', 'drop'):
            return use('turn', {'degrees': 45 * (direction or 1)}, 'search_for_visible_floor_zone')
        return decision, None
    if box is None:
        previous_turn = next((a['args']['degrees'] for a in reversed(history)
                              if a['tool'] == 'turn' and a['result'].get('success')), 45)
        search_turn = 45 if previous_turn >= 0 else -45
        if forward and (depth is None or depth > .6):
            return use('turn', {'degrees': search_turn}, 'reacquire_target_before_blind_drive')
        if decision.name == 'turn' and decision.args['degrees'] * search_turn < 0:
            return use('turn', {'degrees': search_turn}, 'consistent_target_search')
        return decision, None
    center = (box[0] + box[2]) / 2
    bearing = math.degrees(math.atan((center - 320) / 320))
    # Pickup tolerates 30 degrees. Near-field boxes are clipped and their
    # midpoint is less reliable; never demand driving alignment before grasp.
    if (not holding and depth is not None and depth <= .18 and 'pickup' not in disabled
            and abs(bearing) <= 30 and (forward or decision.name in ('pickup', 'turn'))):
        return use('pickup', {}, 'near_target_pickup_before_contact')
    if abs(bearing) > 10 and (forward or reaching):
        return use('turn', {'degrees': round(max(-25, min(25, bearing)), 2)},
                   'align_to_reported_target')
    if (view['path_clear'] and decision.name == 'turn' and abs(bearing) > 10
            and decision.args['degrees'] * bearing < 0):
        return use('turn', {'degrees': round(max(-25, min(25, bearing)), 2)},
                   'turn_toward_visible_target')
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
    if forward and depth is not None and depth <= .25:
        step = min(.03, max(0, depth - .045))
        if step > .005 and decision.args['distance'] > step:
            return use('move', {'distance': math.floor(step * 10000) / 10000}, 'small_near_surface_step')
    if decision.name == 'pickup':
        # A fully visible ground object cannot be within reach for this camera.
        # A clipped object with a distant ray return is NOT a near depth reading.
        step = .2 if not clipped else .05
        if depth is not None and depth < .6:
            step = min(.03 if depth <= .25 else .1, max(0, depth - .055))
        if depth is not None:
            step = min(step, max(0, depth - .04))
        if step > .005:
            return use('move', {'distance': math.floor(step * 10000) / 10000}, 'approach_before_premature_pickup')
    if forward and depth is not None and decision.args['distance'] > max(0, depth - .04):
        step = max(0, depth - .045)
        if step > .005:
            return use('move', {'distance': math.floor(min(.2, step) * 10000) / 10000}, 'range_bounded_approach')
    return decision, None
