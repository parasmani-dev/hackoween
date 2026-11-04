import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { copyFile, mkdir, readFile, readdir, realpath, stat, writeFile } from 'node:fs/promises';
import { dirname, join, relative, resolve, isAbsolute, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pngSize } from './png-size.mjs';

const validator = fileURLToPath(new URL('./validate.mjs', import.meta.url));
const sha256 = data => createHash('sha256').update(data).digest('hex');
const scoredOutcomes = new Set(['pass', 'fail_wrong_object', 'fail_not_reached', 'fail_collision']);
const tools = new Map([
  ['move', { arg: 'distance', result: ['success', 'collided'] }],
  ['turn', { arg: 'degrees', result: ['success'] }],
  ['pickup', { result: ['success'] }],
  ['drop', { result: ['success', 'in_zone'] }],
]);

function hasFields(value, fields) {
  return value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
}

function validToolCall(step) {
  if (!hasFields(step, ['step', 'timestamp', 'tool', 'args', 'result', 'frame_path']) ||
      !Number.isInteger(step.step) || step.step < 1 || typeof step.timestamp !== 'string' ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(step.timestamp) ||
      !Number.isFinite(Date.parse(step.timestamp)) || typeof step.frame_path !== 'string' ||
      !/^(?:[a-zA-Z0-9_-]+\/)*[a-zA-Z0-9_-]+\.png$/.test(step.frame_path)) {
    return false;
  }
  const notDispatched = hasFields(step.result, ['outcome', 'reason']) &&
    step.result.outcome === 'not_dispatched';
  if (step.tool === null) {
    return hasFields(step.args, []) && notDispatched && step.result.reason === 'provider_error';
  }
  const action = tools.get(step.tool);
  if (!action || !hasFields(step.args, action.arg ? [action.arg] : []) ||
      (action.arg && !Number.isFinite(step.args[action.arg]))) return false;
  return (step.tool === 'turn' && notDispatched && step.result.reason === 'navigation_guard') ||
    (hasFields(step.result, ['outcome']) && step.result.outcome === 'uncertain') ||
    (hasFields(step.result, action.result) && action.result.every(field => typeof step.result[field] === 'boolean'));
}

async function readToolLog(runDirectory) {
  const root = await realpath(runDirectory);
  const log = await readFile(join(root, 'steps.jsonl'), 'utf8');
  if (!log.trim()) throw new Error('Empty tool-call log.');
  const frames = new Map();
  for (const [index, line] of log.trimEnd().split(/\r?\n/).entries()) {
    const step = JSON.parse(line);
    if (!validToolCall(step)) {
      throw new Error('Invalid or private tool-call fields at line ' + (index + 1) + '.');
    }
    const key = step.frame_path.toLowerCase();
    if (frames.has(key)) {
      if (frames.get(key).path !== step.frame_path) throw new Error('Decision frame paths differ only by case.');
      continue;
    }
    const frame = await realpath(resolve(root, step.frame_path));
    const fromRoot = relative(root, frame);
    if (fromRoot === '..' || fromRoot.startsWith('..' + sep) || isAbsolute(fromRoot) ||
        !(await stat(frame)).isFile()) {
      throw new Error('Decision frame must be a file inside its agent run directory.');
    }
    const data = await readFile(frame);
    const { width, height } = pngSize(data, step.frame_path, 640, 480);
    if (width !== 640 || height !== 480) throw new Error('Expected a 640×480 decision PNG: ' + step.frame_path);
    try {
      execFileSync(process.env.FFMPEG_PATH || 'ffmpeg', [
        '-nostdin', '-v', 'error', '-xerror', '-abort_on', 'empty_output',
        '-f', 'image2pipe', '-c:v', 'png', '-i', 'pipe:0', '-frames:v', '1', '-f', 'null', '-',
      ], { input: data, stdio: ['pipe', 'ignore', 'pipe'] });
    } catch {
      throw new Error('Could not decode decision PNG: ' + step.frame_path);
    }
    frames.set(key, { path: step.frame_path, data });
  }
  return { log, frames: [...frames.values()] };
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
    if (result.verdict?.state !== 'Done' || !scoredOutcomes.has(result.verdict.outcome) ||
        result.verdict.reason === 'evaluator_unavailable') {
      throw new Error('Expected a scored terminal launcher verdict.');
    }
    if (result.pov_status !== 'complete' ||
        typeof result.pov_capture_id !== 'string' || !result.pov_capture_id.trim() ||
        typeof result.pov_manifest !== 'string' || !isAbsolute(result.pov_manifest)) {
      throw new Error('Expected complete POV status, a capture ID, and a manifest.');
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
    const evidence = await readToolLog(agentRun);
    runIds.add(run_id);
    commands.add(command);
    clips.add(clip);
    agentRuns.add(agentRun);
    prepared.push({ clip, ...evidence });
  }

  const destination = resolve(outputPath);
  await mkdir(destination);
  for (const [index, { clip, log, frames }] of prepared.entries()) {
    const name = 'run-' + (index + 1);
    const evidence = join(destination, name);
    await mkdir(evidence);
    await copyFile(clip, join(destination, name + '.mp4'));
    await writeFile(join(evidence, 'steps.jsonl'), log);
    for (const { path, data } of frames) {
      const target = join(evidence, path);
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, data);
    }
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
