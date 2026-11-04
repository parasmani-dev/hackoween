"""Deterministic local fetch rehearsal using Webots Supervisor poses."""

import math
from pathlib import Path

from controller import Supervisor

output_dir = Path(__file__).resolve().parent


def report(message):
    with (output_dir / "actions.log").open("a", encoding="utf-8") as stream:
        print(message, file=stream)


def run(robot):
    step = int(robot.getBasicTimeStep())
    dt = step / 1000
    camera = robot.getDevice("front_camera")
    camera.enable(step)
    rover = robot.getSelf()
    translation = rover.getField("translation")
    rotation = rover.getField("rotation")
    target = robot.getFromDef("ITEM_1")
    target_translation = target.getField("translation")
    zone_x, zone_y, _ = robot.getFromDef("DROP_ZONE").getPosition()
    x, y, z = translation.getSFVec3f()
    heading = 0.0
    holding = False

    def advance():
        if holding:
            target_translation.setSFVec3f([
                x + 0.51 * math.cos(heading), y + 0.51 * math.sin(heading), 0.32
            ])
            target.resetPhysics()
        if robot.step(step) == -1:
            raise RuntimeError("Simulation stopped before fetch completed")

    def pause(seconds):
        for _ in range(math.ceil(seconds / dt)):
            advance()

    def go_to(goal_x, goal_y):
        nonlocal x, y, heading
        dx, dy = goal_x - x, goal_y - y
        distance = math.hypot(dx, dy)
        if distance < 1e-6:
            return
        desired = math.atan2(dy, dx)
        turn = math.remainder(desired - heading, 2 * math.pi)
        report(f"TURN {math.degrees(turn):.1f} degrees (positive left)")
        start_heading = heading
        turn_steps = max(1, math.ceil(abs(turn) / (math.pi / 2 * dt)))
        for tick in range(1, turn_steps + 1):
            heading = start_heading + turn * tick / turn_steps
            rotation.setSFRotation([0, 0, 1, heading])
            advance()
        report(f"DRIVE {distance:.2f} metres")
        start_x, start_y = x, y
        drive_steps = max(1, math.ceil(distance / dt))
        for tick in range(1, drive_steps + 1):
            x = start_x + dx * tick / drive_steps
            y = start_y + dy * tick / drive_steps
            translation.setSFVec3f([x, y, z])
            advance()

    pause(0.5)
    target_x, target_y, _ = target.getPosition()
    bearing = math.atan2(target_y - y, target_x - x)
    go_to(target_x - 0.44 * math.cos(bearing), target_y - 0.44 * math.sin(bearing))
    target_x, target_y, _ = target.getPosition()
    pickup_angle = math.remainder(math.atan2(target_y - y, target_x - x) - heading, 2 * math.pi)
    if math.hypot(target_x - x, target_y - y) > 0.5 or abs(pickup_angle) > math.pi / 6:
        raise RuntimeError("Target outside pickup reach or heading tolerance")
    report("PICKUP red sphere")
    holding = True
    pause(1)

    go_to(-0.65, y)
    go_to(-0.65, zone_y)
    go_to(zone_x - 0.51, zone_y)
    report("DROP red sphere")
    holding = False
    pause(1.5)
    object_x, object_y, object_z = target.getPosition()
    if not (abs(object_x - zone_x) <= 0.65 and abs(object_y - zone_y) <= 0.65 and object_z < 0.23):
        raise RuntimeError("The red sphere did not land inside the drop zone")


robot = Supervisor()
result_file = Path(__file__).resolve().with_name("result.txt")
try:
    run(robot)
except Exception as exc:
    result = f"FAIL: {exc}"
    exit_code = 1
else:
    result = "PASS: red sphere picked up and delivered to the green drop zone"
    exit_code = 0

result_file.write_text(result, encoding="utf-8")
print(result, flush=True)
robot.simulationQuit(exit_code)
