import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const script = name => fileURLToPath(new URL(name, import.meta.url));
const writeJson = (path, value) => writeFile(path, JSON.stringify(value));

test('submission packages only the complete capture belonging to each attempt', async () => {
  const root = await mkdtemp(join(tmpdir(), 'rover-submission-test-'));
  try {
    const png = join(root, 'frame.png');
    execFileSync(process.env.FFMPEG_PATH || 'ffmpeg', [
      '-v', 'error', '-f', 'lavfi', '-i', 'color=s=960x720',
      '-frames:v', '1', '-threads', '1', png,
    ]);
    const fixtures = [];
    for (let index = 0; index < 3; index++) {
      const attempt = join(root, 'attempt-' + index);
      const agent = join(attempt, 'run-agent');
      await mkdir(agent, { recursive: true });
      await mkdir(join(attempt, 'frames'));
      await copyFile(png, join(agent, '0000.png'));
      await writeFile(join(agent, 'steps.jsonl'), JSON.stringify({
        step: 1, timestamp: new Date(0).toISOString(), tool: 'pickup',
        args: {}, result: { success: true }, frame_path: '0000.png',
      }) + '\n');

      const frames = [];
      for (let second = 0; second < 10; second++) {
        const path = 'frames/' + String(second).padStart(6, '0') + '.png';
        await copyFile(png, join(attempt, path));
        frames.push({ path, sim_time_ms: second * 1000, wall_time_utc: new Date(second * 1000).toISOString() });
      }
      const manifest = join(attempt, 'capture.json');
      const id = 'capture-' + index;
      await writeJson(manifest, { version: 1, run_id: id, command: 'Fetch fixture ' + index, frames });
      const clip = join(attempt, 'clip.mp4');
      execFileSync(process.execPath, [script('./export.mjs'), '--capture', manifest, '1', clip]);
      const resultPath = join(attempt, 'operator-result.json');
      const result = {
        verdict: { outcome: 'pass' }, pov_status: 'complete', pov_capture_id: id, pov_manifest: manifest,
      };
      await writeJson(resultPath, result);
      fixtures.push({ manifest, resultPath, result, run: { attempt, clip, reviewed: true } });
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
    const valid = await packageRuns('valid', runs);
    assert.equal(valid.status, 0, valid.stderr);
    assert.equal((await readdir(valid.output)).length, 7);

    const [first, second] = fixtures;
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
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
