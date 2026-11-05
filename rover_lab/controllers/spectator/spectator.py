from controller import Supervisor
from PIL import Image
from pathlib import Path
import os

robot = Supervisor()
arena = robot.getFromDef("ARENA_CAMERA")
front = robot.getFromDef("FRONT_CAMERA")
viewpoint = robot.getFromDef("WORLD_VIEWPOINT")
# Keep the full wall in the rover POV and reveal its low cutaway in the arena view.
robot.getFromDef("SOUTH_FULL").setVisibility(arena, False)
robot.getFromDef("SOUTH_LOW").setVisibility(front, False)

# The larger parts share the live rover's body and wheel poses.
for part in ("BODY", "WHEEL_0", "WHEEL_1", "WHEEL_2", "WHEEL_3"):
    robot.getFromDef(f"ROVER_{part}").setVisibility(arena, False)
    display = robot.getFromDef(f"SPECTATOR_{part}")
    display.setVisibility(front, False)
    display.setVisibility(viewpoint, False)

period = int(robot.getBasicTimeStep())
camera = robot.getDevice("overhead")
camera.enable(period)
size = (camera.getWidth(), camera.getHeight())
path = Path(os.environ["ROVER_SPECTATOR_FILE"])
pending = path.with_suffix(".tmp")
while robot.step(period) != -1:
    try:
        image = Image.frombytes("RGB", size, camera.getImage(), "raw", "BGRX")
        image.save(pending, "JPEG", quality=90, subsampling=0)
        pending.replace(path)
    except (OSError, ValueError):
        continue
