import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir, realpath, stat, writeFile } from 'node:fs/promises';
import { dirname, join, relative, resolve, isAbsolute, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const validator = fileURLToPath(new URL('./validate.mjs', import.meta.url));
const sha256 = data => createHash('sha256').update(data).digest('hex');

async function readToolLog(runDirectory) {
  const root = await realpath(runDirectory);
  const log = await readFile(join(root, 'steps.jsonl'), 'utf8');
  if (!log.trim()) throw new Error('Empty tool-call log.');
  const checkedFrames = new Set();
  for (const [index, line] of log.trimEnd().split(/\r?\n/).entries()) {
    const step = JSON.parse(line);
    if (!step || !Number.isInteger(step.step) || typeof step.timestamp !== 'string' ||
        typeof step.tool !== 'string' || !Object.hasOwn(step, 'args') ||
        !Object.hasOwn(step, 'result') || typeof step.frame_path !== 'string' ||
        !step.frame_path.toLowerCase().endsWith('.png') || isAbsolute(step.frame_path)) {
      throw new Error('Invalid tool-call entry at line ' + (index + 1) + '.');
    }
    if (checkedFrames.has(step.frame_path)) continue;
    const frame = await realpath(resolve(root, step.frame_path));
    const fromRoot = relative(root, frame);
    if (fromRoot === '..' || fromRoot.startsWith('..' + sep) || isAbsolute(fromRoot) ||
        !(await stat(frame)).isFile()) {
      throw new Error('Decision frame must be a file inside its agent run directory.');
    }
    checkedFrames.add(step.frame_path);
  }
  return log;
}

async function packageSubmission([configPath, outputPath, ...extra]) {
  if (!configPath || !outputPath || extra.length) {
    throw new Error('Usage: node recording/package-submission.mjs <package.json> <new-output-dir>');
  }
  const config = JSON.parse(await readFile(configPath, 'utf8'));
  const { repository, visual_identification, demo_booking, runs } = config;
  if (typeof repository !== 'string' || !/^https:\/\/[^\s]+$/u.test(repository) ||
      typeof visual_identification !== 'string' || !visual_identification.trim() ||
      typeof demo_booking !== 'string' || !demo_booking.trim() ||
      !Array.isArray(runs) || runs.length !== 3) {
    throw new Error('Supply an HTTPS repository link, visual-identification paragraph, demo booking and three runs.');
  }

  const base = dirname(resolve(configPath));
  const runIds = new Set();
  const commands = new Set();
  const clips = new Set();
  const agentRuns = new Set();
  const prepared = [];
  for (const [index, run] of runs.entries()) {
    if (!run || run.reviewed !== true ||
        ![run.clip, run.attempt].every(value => typeof value === 'string' && value.trim())) {
      throw new Error('Run ' + (index + 1) + ' needs a clip, launcher attempt directory and completed visual review.');
    }
    const attempt = await realpath(resolve(base, run.attempt));
    const result = JSON.parse(await readFile(join(attempt, 'operator-result.json'), 'utf8'));
    if (result.verdict?.outcome !== 'pass' || result.pov_status !== 'complete' ||
        typeof result.pov_capture_id !== 'string' || !result.pov_capture_id.trim() ||
        typeof result.pov_manifest !== 'string' || !isAbsolute(result.pov_manifest)) {
      throw new Error('Expected a passing launcher attempt with complete POV status, a capture ID, and a manifest.');
    }
    const capture = await realpath(result.pov_manifest);
    const manifest = await readFile(capture);
    const { run_id, command } = JSON.parse(manifest);
    if (run_id !== result.pov_capture_id) {
      throw new Error('POV manifest run ID does not match the launcher attempt capture ID.');
    }
    const clip = await realpath(resolve(base, run.clip));
    const candidates = (await readdir(attempt, { withFileTypes: true }))
      .filter(entry => entry.isDirectory() && entry.name.startsWith('run-'));
    if (candidates.length !== 1) throw new Error('Expected exactly one agent run in the launcher attempt.');
    const agentRun = join(attempt, candidates[0].name);
    const receipt = JSON.parse(await readFile(clip + '.export.json', 'utf8'));
    if (receipt.manifest_sha256 !== sha256(manifest) || receipt.clip_sha256 !== sha256(await readFile(clip))) {
      throw new Error('Clip and capture must match the receipt from export.mjs --capture.');
    }
    if (runIds.has(run_id) || commands.has(command) || clips.has(clip) || agentRuns.has(agentRun)) {
      throw new Error('Each entry must use a distinct capture, command, clip and agent run.');
    }
    execFileSync(process.execPath, [validator, capture, clip], { stdio: 'inherit' });
    const log = await readToolLog(agentRun);
    runIds.add(run_id);
    commands.add(command);
    clips.add(clip);
    agentRuns.add(agentRun);
    prepared.push({ clip, log });
  }

  const destination = resolve(outputPath);
  await mkdir(destination);
  for (const [index, { clip, log }] of prepared.entries()) {
    const name = 'run-' + (index + 1);
    await copyFile(clip, join(destination, name + '.mp4'));
    await writeFile(join(destination, name + '-steps.jsonl'), log);
  }
  await writeFile(join(destination, 'submission.json'), JSON.stringify({
    repository, visual_identification, demo_booking,
  }, null, 2) + '\n');
  console.log('Prepared three runs in ' + destination);
}

try {
  await packageSubmission(process.argv.slice(2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
