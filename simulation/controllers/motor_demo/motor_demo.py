"""Physical wheel commands and a standalone wall recovery check."""

import math
from pathlib import Path

from controller import Supervisor


class RoverDrive:
    wheel_radius = 0.1
    half_track = 0.19

    def __init__(self, robot):
        self.robot = robot
        self.node = robot.getSelf()
        self.timestep = int(robot.getBasicTimeStep())
        self.dt = self.timestep / 1000
        self.after_step = None
        self.capture_step = None
        self.preview_step = None
        self.motors = [robot.getDevice(f"wheel_{index}") for index in range(4)]
        for motor in self.motors:
            motor.setPosition(float("inf"))
        self.command(0, 0)

    def step(self):
        if self.after_step is not None:
            self.after_step()
        if self.robot.step(self.timestep) == -1:
            raise RuntimeError("Webots stopped during an action")
        if self.capture_step is not None:
            self.capture_step()
        if self.preview_step is not None:
            self.preview_step()

    def command(self, forward, yaw_rate):
        for index, motor in enumerate(self.motors):
            side = 1 if index < 2 else -1
            motor.setVelocity((forward + side * self.half_track * yaw_rate) / self.wheel_radius)

    def stop(self):
        self.command(0, 0)
        # Apply the brake without allowing five extra coasting steps.
        self.node.setVelocity([0.0] * 6)

    def heading(self):
        orientation = self.node.getOrientation()
        return math.atan2(orientation[3], orientation[0])

    def contact(self):
        return bool(self.node.getContactPoints(False))

    def move(self, distance, speed=1.0, *, leave_contact=False):
        if distance == 0:
            return True
        direction = math.copysign(1, distance)
        start_x, start_y, _ = self.node.getPosition()
        heading = self.heading()
        best = 0.0
        stalled = 0.0
        self.command(direction * speed, 0)
        try:
            for _ in range(math.ceil((abs(distance) / speed + 3) / self.dt)):
                remaining = abs(distance) - best
                self.command(direction * min(speed, max(0.01, remaining / (2 * self.dt))), 0)
                self.step()
                x, y, _ = self.node.getPosition()
                progress = direction * ((x - start_x) * math.cos(heading) + (y - start_y) * math.sin(heading))
                touching = self.contact()
                if touching and not leave_contact:
                    return False
                if not touching:
                    leave_contact = False
                if progress >= abs(distance) - 0.002 and not touching:
                    return True
                if progress > best + 0.005:
                    best, stalled = progress, 0.0
                else:
                    stalled += self.dt
                    if stalled >= 2:
                        return False
            return False
        finally:
            self.stop()

    def turn(self, degrees):
        if degrees == 0:
            return True
        # Webots ENU yaw increases counterclockwise. Commands use clockwise.
        target = -math.radians(degrees)
        previous = self.heading()
        traveled = 0.0
        try:
            for _ in range(math.ceil((abs(degrees) / 45 + 4) / self.dt)):
                error = target - traveled
                if abs(error) <= math.radians(2):
                    return True
                # Four-wheel skid steering needs feedback for actual chassis yaw.
                measured_rate = (delta / self.dt) if traveled else 0.0
                desired_rate = math.copysign(min(math.pi / 2, abs(error) / self.dt), error)
                rate = 1.45 * desired_rate + 0.5 * (desired_rate - measured_rate)
                self.command(0, rate)
                self.step()
                if self.contact():
                    return False
                heading = self.heading()
                delta = math.remainder(heading - previous, 2 * math.pi)
                traveled += delta
                previous = heading
            raise RuntimeError("Turn timed out")
        finally:
            self.stop()

    def back_off(self, blocked_distance, approach_heading=None):
        if approach_heading is None:
            approach_heading = self.heading()
        # Contact can spin the chassis. Retreat opposite the original travel
        # direction even if the rover is now facing the other way.
        alignment = math.cos(self.heading() - approach_heading)
        retreat = -math.copysign(0.1, blocked_distance * alignment)
        return self.move(retreat, speed=0.25, leave_contact=True)


def check_no_contact_stall(drive, report):
    """Test-only wheel fault: real physics runs, but commanded wheels stay stopped."""
    original_command = drive.command
    try:
        drive.command = lambda forward, yaw_rate: original_command(0, 0)
        for distance in (0.5, -0.5):
            if drive.contact():
                raise RuntimeError("Stall fixture must start clear of walls")
            started = drive.robot.getTime()
            before = drive.node.getPosition()
            if drive.move(distance):
                raise RuntimeError("Stationary wheels incorrectly completed a move")
            elapsed = drive.robot.getTime() - started
            after = drive.node.getPosition()
            if drive.contact() or math.dist(before, after) > 0.005:
                raise RuntimeError("Stall fixture moved or touched a wall")
            if not 2.0 - 1e-8 <= elapsed <= 2.0 + drive.dt + 1e-8:
                raise RuntimeError(f"Stall timeout outside one physics step: {elapsed}")
            report(f"STALL: no contact, distance={distance}, stopped after {elapsed:.3f} sim s")
    finally:
        drive.command = original_command
        drive.stop()


def run_check(robot, report):
    camera = robot.getDevice("front_camera")
    drive = RoverDrive(robot)
    camera.enable(drive.timestep)
    for _ in range(12):
        drive.step()

    check_no_contact_stall(drive, report)

    if drive.move(-2):
        raise RuntimeError("Reverse command should have hit the west wall")
    if drive.node.getPosition()[0] > -2.65:
        raise RuntimeError("Stopped before reaching the west wall")
    report("BLOCKED: reverse drive stopped at the west wall")
    before = drive.node.getPosition()[0]
    if not drive.back_off(-2):
        raise RuntimeError("Backoff was blocked")
    clearance = drive.node.getPosition()[0] - before
    if not 0.09 <= clearance <= 0.16:
        raise RuntimeError(f"Unexpected backoff distance: {clearance:.3f} m")
    report(f"BACKOFF: {clearance:.3f} m")

    initial_heading = drive.heading()
    if not drive.turn(90):
        raise RuntimeError("Recovery turn was blocked")
    angle = math.degrees(math.remainder(drive.heading() - initial_heading, 2 * math.pi))
    if not -96 <= angle <= -84:
        raise RuntimeError(f"Unexpected turn: {angle:.1f} degrees")
    report(f"TURN: {-angle:.1f} degrees clockwise")
    before = drive.node.getPosition()[1]
    if not drive.move(0.5):
        raise RuntimeError("Recovery drive was blocked")
    traveled = before - drive.node.getPosition()[1]
    if not 0.48 <= traveled <= 0.58:
        raise RuntimeError(f"Unexpected recovery travel: {traveled:.3f} m")
    report(f"DRIVE: {traveled:.3f} m after recovery")


if __name__ == "__main__":
    output = Path(__file__).resolve().parent

    def report(message):
        with (output / "actions.log").open("a", encoding="utf-8") as stream:
            print(message, file=stream)

    robot = Supervisor()
    try:
        run_check(robot, report)
    except Exception as exc:
        result, code = f"FAIL: {exc}", 1
    else:
        result, code = "PASS: wheel motors drove, stopped at the wall, backed off, turned and drove clear", 0
    (output / "result.txt").write_text(result, encoding="utf-8")
    robot.simulationQuit(code)
