import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { constants } from 'node:fs';
import { copyFile, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { pngSize } from './png-size.mjs';

function wrapCommand(command, columns) {
  const words = command.split(/(?<= )/u);
  const lines = [];
  let line = '';
  for (const word of words) {
    if (line && Array.from(line + word).length > columns) {
      lines.push(line);
      line = '';
    }
    line += word;
  }
  lines.push(line);
  return lines.join('\n');
}

function checkOverlay(overlay, width, height, scratch) {
  const check = spawnSync(process.env.FFMPEG_PATH || 'ffmpeg', [
    '-nostdin', '-v', 'info', '-f', 'lavfi', '-i', 'color=black:s=' + width + 'x' + height,
    '-vf', overlay + ',bbox=min_val=32', '-frames:v', '1', '-f', 'null', '-',
  ], { cwd: scratch, encoding: 'utf8' });
  if (check.error) throw check.error;
  if (check.status !== 0) throw new Error('FFmpeg could not check the command overlay: ' + check.stderr);
  const bounds = /x1:(\d+) x2:(\d+) y1:(\d+) y2:(\d+)/.exec(check.stderr);
  if (!bounds || Number(bounds[1]) < 20 || Number(bounds[2]) >= width - 20 ||
      Number(bounds[3]) < 20 || Number(bounds[4]) >= height - 20) {
    throw new Error('Command overlay does not fit with readable margins.');
  }
}

async function exportClip([source, commandPath, rate, destination, ...extra]) {
  let manifest;
  let command;
  const fps = Number(rate);
  if (!source || !commandPath || !destination?.toLowerCase().endsWith('.mp4') ||
      !Number.isInteger(fps) || fps < 1 || fps > 60 || extra.length) {
    throw new Error('Usage: node recording/export.mjs <frames-dir> <command.txt> <fps> <output.mp4>\n' +
      '   or: node recording/export.mjs --capture <capture.json> <fps> <output.mp4>');
  }
  if (source === '--capture') {
    manifest = await readFile(commandPath);
    ({ command } = JSON.parse(manifest));
    source = join(dirname(resolve(commandPath)), 'frames');
  }

  command = manifest ? command : (await readFile(commandPath, 'utf8')).replace(/^\uFEFF/, '').replace(/\r?\n$/, '');
  if (typeof command !== 'string' || !command.trim() || /[\x00-\x1f\x7f]/u.test(command)) {
    throw new Error('command.txt must contain one nonempty line.');
  }

  const frames = (await readdir(source)).filter(name => name.endsWith('.png')).sort();
  const seconds = frames.length / fps;
  if (seconds < 10 || seconds > 20) {
    throw new Error('Capture duration must be 10–20 seconds at the supplied frame rate.');
  }

  const scratch = await mkdtemp(join(tmpdir(), 'rover-export-'));
  try {
    let geometry;
    for (const [index, name] of frames.entries()) {
      if (name !== String(index).padStart(6, '0') + '.png') {
        throw new Error('Expected consecutive PNGs starting at 000000.png.');
      }

      const frame = await readFile(join(source, name));
      const { width, height } = pngSize(frame, name);
      const size = width + 'x' + height;
      if (geometry && geometry !== size) {
        throw new Error('All frames must share the same native dimensions.');
      }
      geometry = size;
      await writeFile(join(scratch, name), frame);
    }

    const [width, height] = geometry.split('x').map(value => Math.ceil(Number(value) / 2) * 2);
    await writeFile(join(scratch, 'command.txt'), wrapCommand(command, Math.floor((width - 60) / 28)));
    const text = 'drawtext=textfile=command.txt:expansion=none:fontcolor=white:fontsize=28:' +
      'line_spacing=6:x=(w-tw)/2:y=h-th-30';
    checkOverlay(text, width, height, scratch);
    const overlay = text + ":box=1:boxcolor=black@0.8:boxborderw=10:enable='lt(n," + (2 * fps) + ")'";
    const result = spawnSync(process.env.FFMPEG_PATH || 'ffmpeg', [
      '-nostdin', '-v', 'error', '-xerror', '-framerate', String(fps), '-start_number', '0',
      '-i', '%06d.png', '-vf', "pad=ceil(iw/2)*2:ceil(ih/2)*2," + overlay,
      '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', 'clip.mp4',
    ], { cwd: scratch, stdio: 'inherit' });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error('FFmpeg export failed.');

    await copyFile(join(scratch, 'clip.mp4'), resolve(destination), constants.COPYFILE_EXCL);
    if (manifest) {
      const sha256 = data => createHash('sha256').update(data).digest('hex');
      await writeFile(destination + '.export.json', JSON.stringify({
        manifest_sha256: sha256(manifest),
        clip_sha256: sha256(await readFile(join(scratch, 'clip.mp4'))),
        timing: {
          policy: 'fixed_frame_rate_one_output_frame_per_capture',
          fps,
          captured_frames: frames.length,
          inference_pauses: 'wall_clock_gaps_not_reconstructed',
          approval: 'pending',
        },
      }, null, 2) + '\n', { flag: 'wx' });
    }
    console.log('Exported ' + seconds + ' seconds to ' + resolve(destination));
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
}

try {
  await exportClip(process.argv.slice(2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
