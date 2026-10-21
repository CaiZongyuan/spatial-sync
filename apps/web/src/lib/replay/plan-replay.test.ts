import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, expect, it, vi } from 'vitest';
import type { EntityEvent, OperationPlan, PlanFullUpdate, PlanStepUpdate, ServerMessage } from '../protocol';
import { ReplaySource } from './replay-source';

let source: ReplaySource;
afterEach(() => { source?.stop(); vi.useRealTimers(); vi.unstubAllGlobals(); });

async function recording() {
  const directory = fileURLToPath(new URL('../../../../../tests/fixtures/recordings/v2/', import.meta.url));
  const metadata = JSON.parse(readFileSync(directory + 'recording.json', 'utf8'));
  const original = readFileSync(directory + metadata.segments[0].file);
  const frameLength = original.readUInt32LE(6);
  const snapshotOffset = 10 + frameLength;
  const snapshotLength = original.readUInt32LE(snapshotOffset);
  const frame = original.subarray(19, 10 + frameLength);
  const info = JSON.parse(original.subarray(snapshotOffset + 13, snapshotOffset + 4 + snapshotLength).toString());
  const origin = original.readDoubleLE(11);
  const scope = { sceneRevision: metadata.sceneRevision, epoch: 0, simTime: 0, sampleTimeMs: origin };
  const plan: OperationPlan = { id: 'recorded-plan', kind: 'operation', title: 'Recorded plan', revision: 1,
    target: 'session', status: 'approved', steps: [
      { id: 1, status: 'pending', do: { entity: '/World/Lab/RobotA', action: 'home', params: {} } },
      { id: 2, status: 'pending', check: { entity: '/World/Lab/RobotA', property: 'phase', equals: 'idle' } },
    ] };
  const full = (value: OperationPlan, at: number, epoch = 0): PlanFullUpdate => ({ type: 'plan.update', update: 'full',
    ...scope, epoch, sampleTimeMs: origin + at, simTime: at / 1000, plan: value });
  const step = (id: number, status: 'running' | 'done', at: number, revision = 1): PlanStepUpdate => ({
    type: 'plan.update', update: 'step', ...scope, sampleTimeMs: origin + at, simTime: at / 1000,
    planId: plan.id, revision, stepId: id, status });
  const revised: OperationPlan = { ...plan, revision: 2, title: 'Revised recorded plan',
    steps: [{ ...plan.steps[0], status: 'done' }, plan.steps[1]] };
  function record(kind: number, at: number, payload: Buffer) {
    const header = Buffer.alloc(13); header.writeUInt32LE(payload.length + 9, 0);
    header.writeUInt8(kind, 4); header.writeDoubleLE(origin + at, 5);
    return Buffer.concat([header, payload]);
  }
  const event = (at: number, value: ServerMessage) => record(2, at, Buffer.from(JSON.stringify(value)));
  const files = new Map<string, Buffer>();
  metadata.segments = [
    [full(plan, 0), step(1, 'running', 100), { type: 'entity.event', ...scope, sampleTimeMs: origin + 200,
      entityKey: '/World/Lab/RobotA', event: 'plan.invocation', data: { planId: plan.id, revision: 1,
        stepId: 1, invocationId: 'recorded-call', outOfPlan: false, outOfOrder: false } } as EntityEvent,
    step(1, 'done', 300), full(revised, 400)],
    [full(revised, 1000), step(2, 'running', 1100, 2), step(2, 'done', 1200, 2),
      full({ ...revised, status: 'completed', steps: revised.steps.map(value => ({ ...value, status: 'done' })) }, 1300)],
    [full({ ...plan, id: 'after-reset', title: 'After reset' }, 2000, 1), full({ ...plan, revision: 99 }, 2200)],
  ].map((events, index) => {
    const offset = index * 1000, epoch = index === 2 ? 1 : 0;
    const spatial = Buffer.from(frame);
    spatial.writeUInt32LE(epoch, 12); spatial.writeBigUInt64LE(BigInt(index + 1), 16);
    spatial.writeDoubleLE(origin + offset, 32);
    const snapshot = { ...info, epoch, sampleTimeMs: origin + offset };
    const bytes = Buffer.concat([original.subarray(0, 6), record(1, offset, spatial),
      record(2, offset, Buffer.from(JSON.stringify(snapshot))), ...events.map(value => event(value.sampleTimeMs - origin, value))]);
    const file = `plan-${index}.bin`; files.set(file, bytes);
    return { ...metadata.segments[0], file, startSampleTimeMs: origin + offset,
      endSampleTimeMs: origin + offset + 500, bytes: bytes.length };
  });
  metadata.durationMs = 2500;
  files.set('recording.json', Buffer.from(JSON.stringify(metadata)));
  const urls: string[] = [];
  vi.stubGlobal('location', new URL('https://example.test/replay/recorded'));
  vi.stubGlobal('WebSocket', class { constructor() { throw new Error('Replay opened live WebSocket'); } });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    if (!url.startsWith('https://example.test/files/plans/')) throw new Error('Replay made a live request');
    const name = new URL(url).pathname.split('/files/plans/')[1];
    return new Response(new Uint8Array(files.get(name) ?? readFileSync(directory + name)));
  }));
  source = new ReplaySource('recorded', 'https://example.test/files/plans/recording.json');
  await source.start();
  return urls;
}

it('rebuilds recorded plans and explicit associations through seek, segment prefixes, rewind and reset', async () => {
  const urls = await recording();
  const current = () => source.plans.getSnapshot().entries[0];
  expect(current().plan).toMatchObject({ revision: 1, steps: [{ status: 'pending' }, { status: 'pending' }] });
  await source.seek(300);
  expect(current().plan.steps[0].status).toBe('done');
  expect(current().associations[1].invocationIds).toEqual(['recorded-call']);
  await source.seek(400);
  expect(current().plan).toMatchObject({ revision: 2, steps: [{ status: 'done' }, { status: 'pending' }] });
  expect(current().changes).toEqual([{ change: 'title' }]);
  await source.seek(1000);
  expect(current().plan.revision).toBe(2);
  expect(current().associations).toEqual({}); // Prefix has no recorded association map; do not guess one.
  expect(current().changes).toBeUndefined();
  await source.seek(1300);
  expect(current().plan.status).toBe('completed');
  await source.seek(2500);
  expect(current().plan).toMatchObject({ id: 'after-reset', revision: 1 });
  expect(source.plans.getSnapshot().entries).toHaveLength(1); // Late epoch0/r99 cannot enter epoch1.
  await source.seek(0);
  expect(current().plan).toMatchObject({ id: 'recorded-plan', revision: 1, steps: [{ status: 'pending' }, {}] });
  expect(urls.every(url => url.startsWith('https://example.test/files/plans/'))).toBe(true);
});

it('uses the same consumer during actual timed playback rollover', async () => {
  vi.useFakeTimers();
  await recording();
  source.play();
  await vi.advanceTimersByTimeAsync(1400);
  expect(source.plans.getSnapshot().entries[0].plan).toMatchObject({ revision: 2, status: 'completed' });
  source.pause();
});
