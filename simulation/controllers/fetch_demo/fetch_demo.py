"""Standalone motor-driven fetch with snap pickup and physical release."""

import math
import sys
from pathlib import Path

from controller import Supervisor

output_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(output_dir.parent / "motor_demo"))
from motor_demo import RoverDrive


def report(message):
    with (output_dir / "actions.log").open("a", encoding="utf-8") as stream:
        print(message, file=stream)


def run(robot):
    drive = RoverDrive(robot)
    robot.getDevice("front_camera").enable(drive.timestep)
    target = robot.getFromDef("ITEM_1")
    target_translation = target.getField("translation")
    zone_x, zone_y, _ = robot.getFromDef("DROP_ZONE").getPosition()

    def pause(seconds):
        for _ in range(math.ceil(seconds / drive.dt)):
            drive.step()

    def face(goal_x, goal_y):
        x, y, _ = drive.node.getPosition()
        desired = math.atan2(goal_y - y, goal_x - x)
        degrees = -math.degrees(math.remainder(desired - drive.heading(), 2 * math.pi))
        report(f"TURN {degrees:.1f} degrees clockwise")
        if not drive.turn(degrees):
            raise RuntimeError("Rover turn was blocked")

    def go_to(goal_x, goal_y, speed=0.65):
        face(goal_x, goal_y)
        x, y, _ = drive.node.getPosition()
        distance = math.hypot(goal_x - x, goal_y - y)
        report(f"DRIVE {distance:.2f} metres")
        if not drive.move(distance, speed=speed):
            raise RuntimeError("Rover was blocked on the fetch route")

    def carry():
        x, y, _ = drive.node.getPosition()
        heading = drive.heading()
        target_translation.setSFVec3f([
            x + 0.52 * math.cos(heading), y + 0.52 * math.sin(heading), 0.32
        ])
        target.resetPhysics()

    pause(0.5)
    target_x, target_y, _ = target.getPosition()
    x, y, _ = drive.node.getPosition()
    bearing = math.atan2(target_y - y, target_x - x)
    go_to(target_x - 0.46 * math.cos(bearing), target_y - 0.46 * math.sin(bearing), speed=0.35)
    face(target_x, target_y)
    x, y, _ = drive.node.getPosition()
    target_x, target_y, _ = target.getPosition()
    distance = math.hypot(target_x - x, target_y - y)
    angle = math.remainder(math.atan2(target_y - y, target_x - x) - drive.heading(), 2 * math.pi)
    if distance > 0.5 or abs(angle) > math.pi / 6:
        raise RuntimeError(f"Pickup outside tolerance ({distance:.3f} m, {math.degrees(angle):.1f} degrees)")
    report(f"PICKUP red sphere ({distance:.3f} m)")
    drive.after_step = carry
    pause(1)

    go_to(-0.65, drive.node.getPosition()[1], speed=0.35)
    go_to(-0.65, zone_y)
    go_to(zone_x - 0.52, zone_y)
    report("DROP red sphere")
    drive.after_step = None
    pause(1.5)
    object_x, object_y, object_z = target.getPosition()
    if not (abs(object_x - zone_x) <= 0.65 and abs(object_y - zone_y) <= 0.65 and object_z < 0.23):
        raise RuntimeError("The red sphere did not land inside the drop zone")


robot = Supervisor()
try:
    run(robot)
except Exception as exc:
    result, code = f"FAIL: {exc}", 1
else:
    result, code = "PASS: wheel-driven rover picked up and delivered the red sphere", 0
(output_dir / "result.txt").write_text(result, encoding="utf-8")
robot.simulationQuit(code)
