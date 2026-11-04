import { execFileSync } from 'node:child_process';
import { readFile, stat } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

async function validate([manifestPath, clipPath, ...extra]) {
  if (!manifestPath || !clipPath || extra.length) {
    throw new Error('Usage: node recording/validate.mjs <capture.json> <clip.mp4>');
  }

  const { version, run_id, command, frames } = JSON.parse(await readFile(manifestPath, 'utf8'));
  if (version !== 1 || typeof run_id !== 'string' || !run_id.trim() ||
      typeof command !== 'string' || !command.trim() || /[\x00-\x1f\x7f]/u.test(command) ||
      !Array.isArray(frames) || !frames.length) {
    throw new Error('Invalid version 1 capture manifest.');
  }

  let lastSim = -1;
  let lastWall = -Infinity;
  for (const [index, frame] of frames.entries()) {
    const name = 'frames/' + String(index).padStart(6, '0') + '.png';
    const wallUtc = frame?.wall_time_utc?.replace(/\+00:00$/, 'Z');
    const wall = Date.parse(wallUtc);
    if (frame?.path !== name || !Number.isFinite(frame.sim_time_ms) ||
        frame.sim_time_ms < 0 || frame.sim_time_ms < lastSim ||
        !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(wallUtc) ||
        !Number.isFinite(wall) || new Date(wall).toISOString() !== wallUtc ||
        wall < lastWall) {
      throw new Error('Invalid path or timestamp for frame ' + index + '.');
    }
    if (!(await stat(resolve(dirname(manifestPath), name))).isFile()) {
      throw new Error('Expected a frame file: ' + name);
    }
    lastSim = frame.sim_time_ms;
    lastWall = wall;
  }

  const probe = JSON.parse(execFileSync(process.env.FFPROBE_PATH || 'ffprobe', [
    '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', resolve(clipPath),
  ], { encoding: 'utf8' }));
  const [video] = probe.streams;
  const seconds = Number(probe.format.duration);
  if (!clipPath.toLowerCase().endsWith('.mp4') || !probe.format.format_name.split(',').includes('mp4') ||
      probe.streams.length !== 1 || video.codec_type !== 'video' || video.codec_name !== 'h264' ||
      video.width !== 1280 || video.height !== 960 || Number(video.nb_read_frames) !== frames.length ||
      !Number.isFinite(seconds) || seconds < 10 || seconds > 20) {
    throw new Error('Expected a silent 1280×960 H.264 MP4 of 10–20 seconds with one frame per manifest entry.');
  }

  console.log(run_id + ': ' + frames.length + ' frames, 1280×960 H.264, ' + seconds + ' seconds.');
}

try {
  await validate(process.argv.slice(2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
