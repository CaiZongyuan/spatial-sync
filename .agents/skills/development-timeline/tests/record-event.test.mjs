import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { appendRecord, makeRecord, parseArguments } from '../scripts/record-event.mjs';

test('records observed command boundaries and outcome without executing a command', () => {
  const directory = mkdtempSync(join(tmpdir(), 'development-timeline-record-'));
  try {
    const file = join(directory, 'events.jsonl');
    const start = makeRecord({ type: 'start', id: 'sdk', kind: 'command', lane: 'API', label: 'SDK', category: 'validation' }, '2026-10-04T10:00:00Z');
    const end = makeRecord({ type: 'end', id: 'sdk', outcome: 'failed' }, '2026-10-04T10:00:12Z');
    appendRecord(file, start); appendRecord(file, end);
    const rows = readFileSync(file, 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(rows[0].event.start, '2026-10-04T10:00:00Z');
    assert.equal(rows[0].event.kind, 'command');
    assert.equal(rows[1].eventId, 'sdk');
    assert.equal(rows[1].outcome, 'failed');
    assert.equal(rows[1].at, '2026-10-04T10:00:12Z');
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('requires explicit end identity and timezone rather than inventing historical times', () => {
  assert.throws(() => makeRecord({ type: 'end', outcome: 'passed' }), /original --id/);
  assert.throws(() => makeRecord({ type: 'point', lane: 'Review', label: 'Saved', at: '2026-10-04T10:00:00' }), /timezone/);
  const point = makeRecord({ type: 'point', lane: 'Review', label: 'Report saved', confidence: 'inferred' }, '2026-10-04T10:00:00Z');
  assert.equal(point.event.kind, 'point');
  assert.equal(point.event.end, undefined);
  assert.equal(point.event.confidence, 'inferred');
});

test('parses repeated evidence and rejects unknown flags or conflicting actions', () => {
  const options = parseArguments(['--journal', 'events.jsonl', 'start', '--lane', 'API', '--label', 'Check', '--evidence', 'one.log', '--evidence', 'two.log']);
  assert.equal(options.evidence.length, 2);
  assert.throws(() => parseArguments(['start', 'end']), /one start/);
  assert.throws(() => parseArguments(['--password', 'hidden']), /Unknown argument/);
  assert.throws(() => parseArguments(['--journal']), /Value required/);
});

test('stdin consumers can import the recording API without triggering the CLI', () => {
  const source = new URL('../scripts/record-event.mjs', import.meta.url).href;
  const result = spawnSync(process.execPath, ['--input-type=module', '-'], {
    input: `import { makeRecord } from ${JSON.stringify(source)};\nconsole.log(makeRecord({type:'point',lane:'PM',label:'Import'}).type);`,
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout.trim(), 'point');
});
