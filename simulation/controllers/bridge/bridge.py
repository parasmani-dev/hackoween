"""Webots controller for the loopback request/response bridge."""

import base64
import json
import logging
import math
import os
import socket
import sys
import tempfile
from pathlib import Path

from controller import Supervisor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "motor_demo"))
from motor_demo import RoverDrive


class Scene:
    def __init__(self, robot):
        self.drive = RoverDrive(robot)
        self.camera = robot.getDevice("front_camera")
        self.camera.enable(self.drive.timestep)
        self.objects = [robot.getFromDef(f"ITEM_{index}") for index in range(1, 7)]
        self.zone = robot.getFromDef("DROP_ZONE").getPosition()
        self.initial = [
            (node, node.getField("translation").getSFVec3f(), node.getField("rotation").getSFRotation())
            for node in [self.drive.node, *self.objects]
        ]
        self.held = None
        self.status = "Idle"
        self.reset()

    def reset(self):
        self.held = None
        self._delivered_fixture_key = None
        self.status = "Idle"
        self.drive.after_step = None
        self.drive.command(0, 0)
        for node, position, rotation in self.initial:
            node.getField("translation").setSFVec3f(position)
            node.getField("rotation").setSFRotation(rotation)
            node.resetPhysics()
        self.drive.step()

    def state(self):
        x, y, _ = self.drive.node.getPosition()
        return dict(state=self.status, x=x, y=y, heading=-math.degrees(self.drive.heading()),
                    holding=self.held is not None, retry_count=0)

    def look(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.jpg"
            if self.camera.saveImage(str(path), 90) != 0:
                raise RuntimeError("Camera capture failed")
            return base64.b64encode(path.read_bytes()).decode("ascii")

    def move(self, distance):
        approach_heading = self.drive.heading()
        success = self.drive.move(distance)
        if not success:
            self.drive.back_off(distance, approach_heading)
        self.status = ("Carry" if self.held else "Go to object") if success else "Blocked"
        return dict(success=success, collided=not success)

    def turn(self, degrees):
        return dict(success=self.drive.turn(degrees))

    def carry(self):
        x, y, _ = self.drive.node.getPosition()
        heading = self.drive.heading()
        self.held.getField("translation").setSFVec3f([
            x + 0.52 * math.cos(heading), y + 0.52 * math.sin(heading), self.carry_height
        ])
        self.held.resetPhysics()

    def pickup(self):
        if self.held:
            return dict(success=False)
        x, y, _ = self.drive.node.getPosition()
        heading = self.drive.heading()
        candidates = []
        for node in self.objects:
            px, py, pz = node.getPosition()
            distance = math.hypot(px - x, py - y)
            angle = math.remainder(math.atan2(py - y, px - x) - heading, math.tau)
            if distance <= 0.5 and abs(angle) <= math.pi / 6:
                candidates.append((distance, node, pz))
        if not candidates:
            return dict(success=False)
        _, self.held, height = min(candidates, key=lambda candidate: candidate[0])
        self.carry_height = height + 0.15
        self.drive.after_step = self.carry
        self.carry()
        self.drive.step()
        self.status = "Carry"
        return dict(success=True)

    def drop(self):
        self._delivered_fixture_key = None
        if not self.held:
            return dict(success=False, in_zone=False)
        released = self.held
        self.held = None
        self.drive.after_step = None
        for _ in range(math.ceil(1.5 / self.drive.dt)):
            self.drive.step()
        x, y, _ = released.getPosition()
        in_zone = abs(x - self.zone[0]) <= 0.65 and abs(y - self.zone[1]) <= 0.65
        if in_zone:
            self._delivered_fixture_key = f"item_{self.objects.index(released) + 1}"
        self.status = "Done" if in_zone else "Drop"
        return dict(success=True, in_zone=in_zone)

    def delivered_fixture_key(self):
        return self._delivered_fixture_key


def serve(scene, port):
    handlers = {name: getattr(scene, name) for name in
                ("state", "look", "move", "turn", "pickup", "drop", "reset", "delivered_fixture_key")}
    with socket.create_server(("127.0.0.1", port)) as listener:
        logging.info("Webots bridge listening on loopback port %s", port)
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(3)
                method = None
                try:
                    with connection.makefile("rb") as stream:
                        request = json.loads(stream.readline(4096))
                    method, args = request["method"], request["args"]
                    if method in ("move", "turn"):
                        bound = 10 if method == "move" else 360
                        if len(args) != 1 or type(args[0]) not in (float, int) or not abs(args[0]) <= bound:
                            raise ValueError("Invalid motion argument")
                    elif args:
                        raise ValueError("Unexpected arguments")
                    response = {"ok": True, "result": handlers[method](*args)}
                except Exception:
                    logging.exception("Webots bridge request %r failed at rover state %s", method, scene.state())
                    response = {"ok": False}
                try:
                    connection.sendall(json.dumps(response, allow_nan=False).encode() + b"\n")
                except OSError:
                    logging.warning("Bridge client disconnected before response")


if __name__ == "__main__":
    logging.basicConfig(
        filename=Path(__file__).resolve().parents[2] / "bridge.log",
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.INFO,
    )
    serve(Scene(Supervisor()), int(os.environ.get("ROVER_WEBOTS_PORT", "8765")))
