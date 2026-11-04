import { spawnSync } from 'node:child_process';
import { constants } from 'node:fs';
import { copyFile, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

async function exportClip([source, commandPath, rate, destination, ...extra]) {
  const fps = Number(rate);
  if (!source || !commandPath || !destination?.toLowerCase().endsWith('.mp4') ||
      !Number.isInteger(fps) || fps < 1 || fps > 60 || extra.length) {
    throw new Error('Usage: node recording/export.mjs <frames-dir> <command.txt> <fps> <output.mp4>');
  }

  const command = (await readFile(commandPath, 'utf8')).replace(/^\uFEFF/, '').replace(/\r?\n$/, '');
  if (!command.trim() || /[\x00-\x1f\x7f]/u.test(command)) {
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
      if (frame.length < 33 || frame.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a' ||
          frame.toString('ascii', 12, 16) !== 'IHDR') {
        throw new Error('Invalid PNG: ' + name);
      }

      const width = frame.readUInt32BE(16);
      const height = frame.readUInt32BE(20);
      const size = width + 'x' + height;
      if (width < 960 || height < 720 || (geometry && geometry !== size)) {
        throw new Error('All frames must share native dimensions of at least 960×720.');
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
