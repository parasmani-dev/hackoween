from controller import Supervisor
from PIL import Image
from pathlib import Path
import os
import time

robot = Supervisor()
# Keep the full wall in the rover POV and reveal its low cutaway in the arena view.
robot.getFromDef("SOUTH_FULL").setVisibility(robot.getFromDef("ARENA_CAMERA"), False)
robot.getFromDef("SOUTH_LOW").setVisibility(robot.getFromDef("FRONT_CAMERA"), False)
period = int(robot.getBasicTimeStep())
camera = robot.getDevice("overhead")
camera.enable(period)
size = (camera.getWidth(), camera.getHeight())
path = Path(os.environ["ROVER_SPECTATOR_FILE"])
pending = path.with_suffix(".tmp")
last = 0
while robot.step(period) != -1:
    now = time.monotonic()
    if now - last < .08:
        continue
    try:
        image = Image.frombytes("RGB", size, camera.getImage(), "raw", "BGRX")
        image.save(pending, "JPEG", quality=90, subsampling=0)
        pending.replace(path)
    except (OSError, ValueError):
        continue
    last = now
