"""Enable the front camera for inspection in Webots."""

from controller import Robot

robot = Robot()
timestep = int(robot.getBasicTimeStep())
robot.getDevice("front_camera").enable(timestep)

while robot.step(timestep) != -1:
    pass
