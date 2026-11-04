# Simulator

Open `simulation/worlds/fetch_room.wbt` in Webots R2025a or later and press Play. Inspect the rover camera overlay and confirm the four walls, six objects and green drop zone. The preview controller only enables the camera and advances the scene for inspection. The command-driven controller will replace it in Phase 2.

The world uses ENU coordinates with Z up. The rover begins facing +X. The four wheels are visual geometry in this preview. Static checks cover scene structure and camera configuration. Rendering, collision behavior and fetch actions require Webots runtime verification.
