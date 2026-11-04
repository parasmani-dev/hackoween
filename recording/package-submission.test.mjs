import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { appendFile, copyFile, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const script = name => fileURLToPath(new URL(name, import.meta.url));
const writeJson = (path, value) => writeFile(path, JSON.stringify(value));

test('submission packages only the complete capture belonging to each attempt', async () => {
  const root = await mkdtemp(join(tmpdir(), 'rover-submission-test-'));
  try {
    const makePng = (size, name) => {
      const path = join(root, name);
      execFileSync(process.env.FFMPEG_PATH || 'ffmpeg', [
        '-v', 'error', '-f', 'lavfi', '-i', 'color=s=' + size,
        '-frames:v', '1', '-threads', '1', path,
      ]);
      return path;
    };
    const png = makePng('960x720', 'frame.png');
    const decisionPng = makePng('640x480', 'decision.png');
    const fixtures = [];
    for (let index = 0; index < 3; index++) {
      const attempt = join(root, 'attempt-' + index);
      const agent = join(attempt, 'run-agent');
      await mkdir(join(agent, 'frames'), { recursive: true });
      await mkdir(join(attempt, 'frames'));
      const decision = join(agent, 'frames', '0000.png');
      await copyFile(decisionPng, decision);
      await writeFile(join(agent, 'claude_requests.jsonl'), 'private fixture\n');
      const step = {
        step: 1, timestamp: new Date(0).toISOString(), tool: 'pickup',
        args: {}, result: { success: true }, frame_path: 'frames/0000.png',
      };
      await writeFile(join(agent, 'steps.jsonl'),
        [
          step,
          { ...step, step: 2, result: { outcome: 'uncertain' } },
          { ...step, step: 3, tool: 'turn', args: { degrees: 45 },
            result: { outcome: 'not_dispatched', reason: 'navigation_guard' } },
          { ...step, step: 4, tool: null, result: { outcome: 'not_dispatched', reason: 'provider_error' } },
        ]
          .map(row => JSON.stringify(row)).join('\n') + '\n');

      const frames = [];
      for (let second = 0; second < 10; second++) {
        const path = 'frames/' + String(second).padStart(6, '0') + '.png';
        await copyFile(png, join(attempt, path));
        frames.push({ path, sim_time_ms: second * 1000, wall_time_utc: new Date(second * 1000).toISOString() });
      }
      const manifest = join(attempt, 'capture.json');
      const id = 'capture-' + index;
      const command = index === 0
        ? 'Fetch the red sphere from the far side of the room and drop it inside the green zone without touching the blue cylinder or the yellow cone.'
        : 'Fetch fixture ' + index;
      await writeJson(manifest, { version: 1, run_id: id, command, frames });
      const originalManifest = await readFile(manifest);
      const clip = join(attempt, 'clip.mp4');
      execFileSync(process.execPath, [script('./export.mjs'), '--capture', manifest, '1', clip]);
      assert.deepEqual(await readFile(manifest), originalManifest);
      const receipt = JSON.parse(await readFile(clip + '.export.json', 'utf8'));
      assert.deepEqual(receipt.timing, {
        policy: 'fixed_frame_rate_one_output_frame_per_capture',
        fps: 1, captured_frames: 10, inference_pauses: 'wall_clock_gaps_not_reconstructed', approval: 'pending',
      });
      const resultPath = join(attempt, 'operator-result.json');
      const result = {
        verdict: { state: 'Done', outcome: ['pass', 'fail_wrong_object', 'fail_not_reached'][index] },
        pov_status: 'complete', pov_capture_id: id, pov_manifest: manifest,
      };
      await writeJson(resultPath, result);
      fixtures.push({ manifest, resultPath, result, decision, agent, run: { attempt, clip, reviewed: true } });
    }

    const config = join(root, 'config.json');
    const packageRuns = async (name, runs) => {
      await writeJson(config, {
        repository: 'https://example.com/rover', visual_identification: 'Fixture',
        demo_booking: 'Fixture', runs,
      });
      const output = join(root, name);
      return { ...spawnSync(process.execPath, [
        script('./package-submission.mjs'), config, output,
      ], { encoding: 'utf8' }), output };
    };
    const runs = fixtures.map(fixture => fixture.run);
    // Inspect the decoded frames directly: the black fixture gains bright pixels only during the overlay.
    const bounds = spawnSync(process.env.FFMPEG_PATH || 'ffmpeg', [
      '-v', 'info', '-i', runs[0].clip, '-vf', 'bbox=min_val=32', '-frames:v', '3', '-f', 'null', '-',
    ], { encoding: 'utf8' });
    assert.equal(bounds.status, 0, bounds.stderr);
    assert.deepEqual([...bounds.stderr.matchAll(/n:(\d+) pts:.*x1:/g)].map(match => Number(match[1])), [0, 1]);
    const valid = await packageRuns('valid', runs);
    assert.equal(valid.status, 0, valid.stderr);
    assert.equal((await readdir(valid.output)).length, 7);
    for (const [index, fixture] of fixtures.entries()) {
      const evidence = join(valid.output, 'run-' + (index + 1));
      assert.deepEqual((await readdir(evidence)).sort(), ['frames', 'steps.jsonl']);
      assert.deepEqual(await readdir(join(evidence, 'frames')), ['0000.png']);
      assert.deepEqual(await readFile(join(evidence, 'steps.jsonl')), await readFile(join(fixture.agent, 'steps.jsonl')));
      assert.deepEqual(await readFile(join(evidence, 'frames', '0000.png')), await readFile(fixture.decision));
    }

    const [first, second] = fixtures;
    await writeJson(first.resultPath, { ...first.result, verdict: { state: 'Done', outcome: 'fail_collision' } });
    const collision = await packageRuns('collision', runs);
    assert.equal(collision.status, 0, collision.stderr);
    for (const [name, verdict] of [
      ['unscored', { state: 'Done', outcome: null, reason: 'evaluator_unavailable' }],
      ['unknown', { state: 'Done', outcome: 'other' }],
      ['nonterminal', { state: 'Blocked', outcome: 'fail_collision' }],
      ['unavailable', { state: 'Done', outcome: 'pass', reason: 'evaluator_unavailable' }],
    ]) {
      await writeJson(first.resultPath, { ...first.result, verdict });
      const rejected = await packageRuns(name, runs);
      assert.equal(rejected.status, 1, rejected.stderr);
      assert.match(rejected.stderr, /scored terminal/);
      await assert.rejects(readdir(rejected.output), { code: 'ENOENT' });
    }
    await writeJson(first.resultPath, { ...first.result, pov_manifest: second.manifest });
    await writeJson(second.resultPath, { ...second.result, pov_manifest: first.manifest });
    const swapped = await packageRuns('swapped', [
      { ...runs[0], clip: runs[1].clip }, { ...runs[1], clip: runs[0].clip }, runs[2],
    ]);
    assert.equal(swapped.status, 1, swapped.stderr);
    assert.match(swapped.stderr, /manifest run ID does not match/);
    await assert.rejects(readdir(swapped.output), { code: 'ENOENT' });

    for (const [name, fields] of [
      ['missing-id', { pov_capture_id: undefined }],
      ['blank-id', { pov_capture_id: ' ' }],
      ['missing-status', { pov_status: undefined }],
      ['incomplete', { pov_status: 'incomplete' }],
    ]) {
      await writeJson(first.resultPath, { ...first.result, ...fields });
      const rejected = await packageRuns(name, runs);
      assert.equal(rejected.status, 1, rejected.stderr);
      assert.match(rejected.stderr, /complete POV status/);
      await assert.rejects(readdir(rejected.output), { code: 'ENOENT' });
    }
    await writeJson(first.resultPath, first.result);
    await writeJson(second.resultPath, second.result);
    await rm(first.decision);
    const missing = await packageRuns('missing-image', runs);
    assert.equal(missing.status, 1, missing.stderr);
    assert.match(missing.stderr, /ENOENT/);
    await assert.rejects(readdir(missing.output), { code: 'ENOENT' });
    await copyFile(png, first.decision);
    const native = await packageRuns('native-in-log', runs);
    assert.equal(native.status, 1, native.stderr);
    assert.match(native.stderr, /640×480 decision PNG/);
    await writeFile(first.decision, (await readFile(decisionPng)).subarray(0, 33));
    const truncated = await packageRuns('truncated-image', runs);
    assert.equal(truncated.status, 1, truncated.stderr);
    assert.match(truncated.stderr, /Could not decode decision PNG/);
    await assert.rejects(readdir(truncated.output), { code: 'ENOENT' });
    await copyFile(decisionPng, first.decision);

    const logPath = join(first.agent, 'steps.jsonl');
    const log = await readFile(logPath, 'utf8');
    const step = JSON.parse(log.split('\n')[0]);
    for (const [name, fields] of [
      ['private-top-level', { authorization: 'private fixture' }],
      ['private-arg', { args: { fixture_key: 'private fixture' } }],
      ['private-result', { result: { success: true, verdict: 'private fixture' } }],
      ['private-provider-reason', { tool: null, result: { outcome: 'not_dispatched', reason: 'raw provider error' } }],
      ['null-dispatched-tool', { tool: null }],
      ['provider-error-with-args', { tool: null, args: { degrees: 45 },
        result: { outcome: 'not_dispatched', reason: 'provider_error' } }],
    ]) {
      await writeFile(logPath, JSON.stringify({ ...step, ...fields }) + '\n');
      const rejected = await packageRuns(name, runs);
      assert.equal(rejected.status, 1, rejected.stderr);
      assert.match(rejected.stderr, /Invalid or private tool-call fields/);
      await assert.rejects(readdir(rejected.output), { code: 'ENOENT' });
    }
    await writeFile(logPath, log);

    const manifest = await readFile(first.manifest);
    await appendFile(first.manifest, '\n');
    const changedManifest = await packageRuns('changed-manifest', runs);
    assert.equal(changedManifest.status, 1, changedManifest.stderr);
    assert.match(changedManifest.stderr, /match the receipt/);
    await writeFile(first.manifest, manifest);
    await appendFile(first.run.clip, 'tamper');
    const changedClip = await packageRuns('changed-clip', runs);
    assert.equal(changedClip.status, 1, changedClip.stderr);
    assert.match(changedClip.stderr, /match the receipt/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
