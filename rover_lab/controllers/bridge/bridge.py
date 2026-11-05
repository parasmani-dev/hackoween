"""Webots controller for the loopback request/response bridge."""

import base64
import json
import logging
import math
import os
import socket
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from controller import Supervisor
from PIL import Image

LAB = Path(__file__).resolve().parents[2]
RUN_DIR = Path(os.environ["ROVER_LAB_RUN_DIR"])
sys.path.insert(0, str(LAB.parent / "simulation" / "controllers" / "motor_demo"))
from motor_demo import RoverDrive


class Scene:
    def __init__(self, robot):
        self.drive = RoverDrive(robot)
        self.camera = robot.getDevice("front_camera")
        self.camera.enable(self.drive.timestep)
        self.range_sensor = robot.getDevice("forward_range")
        self.range_sensor.enable(self.drive.timestep)
        self.fixture_names = [f"ITEM_{index}" for index in range(1, 7)]
        self.objects = [robot.getFromDef(name) for name in self.fixture_names]
        self.floor_heights = [node.getPosition()[2] for node in self.objects]
        self.fixture_nodes = [node.exportString() for node in self.objects]
        self.world_children = robot.getRoot().getField("children")
        layout_path = os.environ.get("ROVER_DEMO_LAYOUT")
        self.layout_path = Path(layout_path) if layout_path else None
        if self.layout_path:
            self.partition = robot.getFromDef("PARTITION").getField("translation")
            self.layouts = json.loads((LAB / "layouts.json").read_text(encoding="utf-8-sig"))
        self.zone = robot.getFromDef("DROP_ZONE").getPosition()
        self.initial_position = self.drive.node.getField("translation").getSFVec3f()
        self.initial_rotation = self.drive.node.getField("rotation").getSFRotation()
        self.held = None
        self.capture = None
        self.preview_path = Path(os.environ["ROVER_PREVIEW_FILE"]) if os.environ.get("ROVER_PREVIEW_FILE") else None
        if self.preview_path is not None:
            self.preview_session = uuid4().hex
            self.preview_ready = False
            self.drive.preview_step = self.publish_preview
            self.preview_heartbeat()
        self.status = "Idle"
        self.reset()

    def reset(self):
        if self.capture is not None:
            self.drive.capture_step = None
            self.writer.shutdown(wait=True, cancel_futures=True)
            self.capture = None
        self.held = None
        self._delivered_fixture_key = None
        self.status = "Idle"
        self.drive.after_step = None
        self.drive.command(0, 0)
        # Flush pending attachment updates before removing their physics bodies.
        # Reset is outside the active run/capture.
        self.drive.step()
        # Restoring transforms can leave released bodies with residual motion.
        # Rebuild the six fixture bodies from their original scene definitions.
        for node in self.objects:
            node.remove()
        for definition in self.fixture_nodes:
            self.world_children.importMFNodeFromString(-1, definition)
        self.objects = [self.drive.robot.getFromDef(name) for name in self.fixture_names]
        if self.layout_path:
            layout = self.layouts[self.layout_path.read_text(encoding="utf-8").strip()]
            self.partition.setSFVec3f(layout["partition"])
            for name, position in layout["objects"].items():
                index = self.fixture_names.index(name)
                node = self.objects[index]
                node.getField("translation").setSFVec3f([*position, self.floor_heights[index]])
                node.resetPhysics()
        rover = self.drive.node
        rover.getField("translation").setSFVec3f(self.initial_position)
        rover.getField("rotation").setSFRotation(self.initial_rotation)
        rover.resetPhysics()
        self.drive.step()

    def state(self):
        x, y, _ = self.drive.node.getPosition()
        return dict(state=self.status, x=x, y=y, heading=-math.degrees(self.drive.heading()),
                    holding=self.held is not None, retry_count=0)

    def camera_pixels(self):
        size = (self.camera.getWidth(), self.camera.getHeight())
        return Image.frombytes("RGB", size, self.camera.getImage(), "raw", "BGRX")

    def look(self):
        with BytesIO() as stream:
            self.camera_pixels().resize((640, 480), Image.Resampling.LANCZOS).save(
                stream, "JPEG", quality=90
            )
            return base64.b64encode(stream.getvalue()).decode("ascii")

    def depth(self):
        # One physical ray, not supervisor object locations. A carried object
        # occludes this ray; report unavailable rather than treating it as free floor.
        if self.held is not None:
            return None
        value = self.range_sensor.getValue()
        return float(value) if math.isfinite(value) and 0 <= value < 10 else None

    def preview_heartbeat(self):
        # Wall-clock liveness only: idle heartbeats never step simulation/camera.
        if not self.preview_path:
            return
        try:
            self.preview_path.parent.mkdir(parents=True, exist_ok=True)
            status = Path(str(self.preview_path) + ".json")
            pending = Path(str(status) + ".tmp")
            pending.write_text(json.dumps({"session": self.preview_session,
                                           "ready": self.preview_ready,
                                           "heartbeat_ms": time.time() * 1000}))
            pending.replace(status)
        except OSError:
            pass

    def publish_preview(self):
        # Operator-only feed; never advances physics or enters agent context.
        try:
            pixels = self.camera_pixels()
            pending = self.preview_path.with_suffix(".tmp")
            pixels.save(pending, "JPEG", quality=90, subsampling=0)
            try:
                pending.replace(self.preview_path)
            except PermissionError:
                # A Windows reader can briefly hold the published frame open.
                return
            self.preview_ready = True
        except (OSError, ValueError):
            # Webots can raise ValueError("NULL pointer access") before a frame exists.
            # Keep the last good JPEG, but never advertise it as a current frame.
            self.preview_ready = False
        self.preview_heartbeat()

    def start_capture(self, command):
        if self.capture is not None:
            raise RuntimeError("POV capture already active")
        run_id = uuid4().hex
        root = RUN_DIR / "pov" / run_id
        (root / "frames").mkdir(parents=True)
        self.capture = (root, self.drive.robot.getTime(), {
            "version": 1, "run_id": run_id, "command": command, "frames": []
        })
        self.writer = ThreadPoolExecutor(max_workers=2)
        self.pending = deque()
        self.record_frame()
        self.drive.capture_step = self.record_frame
        return run_id

    @staticmethod
    def save_frame(raw, size, path):
        Image.frombytes("RGB", size, raw, "raw", "BGRX").save(path, compress_level=1)

    def record_frame(self, force=False):
        root, start_time, manifest = self.capture
        frames = manifest["frames"]
        sim_time_ms = round((self.drive.robot.getTime() - start_time) * 1000)
        if not force and frames and frames[-1]["sim_time_ms"] == sim_time_ms:
            return
        relative = f"frames/{len(frames):06d}.png"
        wall_time = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        # Copy camera memory on the Webots thread; workers only encode immutable bytes.
        raw = bytes(self.camera.getImage())
        if len(self.pending) >= 8:
            self.pending.popleft().result()
        self.pending.append(self.writer.submit(self.save_frame, raw,
                            (self.camera.getWidth(), self.camera.getHeight()), root / relative))
        frames.append({
            "path": relative,
            "sim_time_ms": sim_time_ms,
            "wall_time_utc": wall_time,
        })

    def finish_capture(self):
        if self.capture is None:
            return None
        root, _, manifest = self.capture
        self.drive.capture_step = None
        self.record_frame()
        try:
            for future in self.pending:
                future.result()
        finally:
            self.writer.shutdown(wait=True)
        pending = root / "capture.tmp"
        pending.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        path = root / "capture.json"
        pending.replace(path)
        self.capture = None
        return str(path)

    def move(self, distance):
        approach_heading = self.drive.heading()
        # Ramp straight travel while retaining the calibrated turn and retreat commands.
        for motor in self.drive.motors:
            motor.setAcceleration(100.0)
        try:
            success = self.drive.move(distance)
        finally:
            for motor in self.drive.motors:
                motor.setAcceleration(-1.0)
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
        # Carry at floor height: snap attachment requires no grasp/lift physics.
        self.carry_height = self.floor_heights[self.objects.index(self.held)] + 0.006
        self.drive.after_step = self.carry
        # Apply attachment on the next commanded motion, not a physics tick here.
        self.status = "Carry"
        return dict(success=True)

    def drop(self):
        self._delivered_fixture_key = None
        if not self.held:
            return dict(success=False, in_zone=False)
        released = self.held
        self.held = None
        self.drive.after_step = None
        # Release where the attached object already rests. No settling animation,
        # no relocation toward the zone, and no physics clock advancement.
        released.setVelocity([0.0] * 6)
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
                ("state", "look", "depth", "move", "turn", "pickup", "drop", "reset", "delivered_fixture_key",
                 "start_capture", "finish_capture")}
    with socket.create_server(("127.0.0.1", port)) as listener:
        logging.info("Webots bridge listening on loopback port %s", port)
        listener.settimeout(0.5)
        while True:
            scene.preview_heartbeat()
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
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
                    elif method == "start_capture":
                        if len(args) != 1 or not isinstance(args[0], str) or not args[0].strip():
                            raise ValueError("Expected a capture command")
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
        filename=RUN_DIR / "bridge.log",
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.INFO,
    )
    serve(Scene(Supervisor()), int(os.environ.get("ROVER_WEBOTS_PORT", "8765")))
