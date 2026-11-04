import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readPreview } from './preview.mjs';

test('preview rejects previous sessions and dead producers, permits idle heartbeats', () => {
  const root = mkdtempSync(join(tmpdir(), 'preview-'));
  const file = join(root, 'preview.jpg');
  const status = (value) => writeFileSync(`${file}.json`, JSON.stringify(value));
  try {
    writeFileSync(file, 'last good frame');
    assert.throws(() => readPreview(file, 10000)); // pre-heartbeat stale file
    status({session:'previous', ready:true, heartbeat_ms:1});
    assert.throws(() => readPreview(file, 10000)); // dead process
    status({session:'new', ready:false, heartbeat_ms:10000});
    assert.throws(() => readPreview(file, 10000)); // new session has no frame
    status({session:'new', ready:true, heartbeat_ms:10000});
    assert.equal(readPreview(file, 10001).toString(), 'last good frame');
    status({session:'new', ready:true, heartbeat_ms:20000});
    assert.equal(readPreview(file, 20001).toString(), 'last good frame'); // unchanged idle image
    assert.throws(() => readPreview(file, 26000)); // producer died
    status({session:'new', ready:false, heartbeat_ms:26000});
    assert.throws(() => readPreview(file, 26001)); // missing buffer while process alive
    status({session:'new', ready:true, heartbeat_ms:'26000'});
    assert.throws(() => readPreview(file, 26001));
  } finally { rmSync(root, {recursive:true, force:true}); }
});
