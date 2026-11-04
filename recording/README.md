# Rover POV export

Use Node.js and FFmpeg with drawtext and libx264. Put FFmpeg on PATH or set FFMPEG_PATH to its absolute executable path.

    node recording/export.mjs <frames-dir> <command.txt> <fps> <output.mp4>
    ffprobe -v error -show_entries stream=codec_name,width,height,nb_frames:format=duration -of json <output.mp4>

Supply consecutive native front-camera PNGs starting at 000000.png, captured at the declared fixed rate from leaving Idle through Done. Use a consistent resolution of at least 960×720. Store the exact typed command in a one-line UTF-8 file. Export requires a 10–20 second sequence and overlays the command for two seconds. Inspect the output for a readable, unclipped command, robot POV throughout, and the complete ending.

Store capture frames and MP4s separately from agent artifacts. The exporter reads only local files. A live capture source and the approved treatment of inference pauses are pending.

## Operator capture fixture

The proposed capture format stores the exact command and frame list in capture.json.
Version 1 has fields version, run_id, command, and frames. Each frame entry
has path (frames/000000.png onward), sim_time_ms (elapsed simulation time),
and wall_time_utc (UTC ISO 8601 with milliseconds). Both clocks are nondecreasing.
Publish the manifest after completing the frame sequence. Keep the capture
separate from Claude request artifacts.

Export the manifest command to command.txt and supply the known fixture rate.
For a 10 FPS fixture, run:

    node recording/export.mjs <capture-dir>/frames <capture-dir>/command.txt 10 <output.mp4>
    node recording/validate.mjs <capture-dir>/capture.json <output.mp4>

Put ffprobe on PATH or set FFPROBE_PATH. The validator checks the manifest,
frame paths and timestamps, and the clip's decoded frame count, format,
resolution and duration. Review the overlay, image content, run boundaries
and timing separately. Label timing-adjusted exports as rehearsals while the
mentor ruling is pending. Keep the raw timestamps for later export.
