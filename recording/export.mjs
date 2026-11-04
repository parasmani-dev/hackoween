import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { constants } from 'node:fs';
import { copyFile, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { pngSize } from './png-size.mjs';

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

    await writeFile(join(scratch, 'command.txt'), command);
    const overlay = "drawtext=textfile=command.txt:expansion=none:fontcolor=white:fontsize=28:" +
      "box=1:boxcolor=black@0.8:boxborderw=10:x=(w-tw)/2:y=h-th-30:enable='lt(n," + (2 * fps) + ")'";
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
      }) + '\n', { flag: 'wx' });
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
