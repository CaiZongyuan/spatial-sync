import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, expect, it, vi } from 'vitest';
import { ReplaySource } from './replay-source';

const fixture = fileURLToPath(new URL('../../../../../tests/fixtures/recordings/v1/', import.meta.url));
const golden = JSON.parse(readFileSync(fixture + '/expected.json', 'utf8')) as {
  tubeKey: string; robotKey: string;
  samples: { timeMs: number; seq: number; tubePose: { p: number[]; q: number[] };
    tubeLocation: string; robotJoints: number[] }[];
};
let source: ReplaySource;

afterEach(() => {
  source?.stop();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('reconstructs ten independently extracted log states and poses from the published v1 fixture', async () => {
  const urls: string[] = [];
  vi.stubGlobal('location', { href: 'https://example.org/replay/v1' });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    const name = new URL(url).pathname.split('/v1/')[1];
    return new Response(new Uint8Array(readFileSync(fixture + '/' + name)));
  }));
  source = new ReplaySource('v1', 'https://example.org/files/v1/recording.json');
  await source.start();
  expect(source.readOnly).toBe(true);
  expect(source.info.getMetadata().ready).toBe(true);
  for (const sample of golden.samples) {
    await source.seek(sample.timeMs);
    expect(source.buffer.latestSample?.seq).toBe(sample.seq);
    expect(source.buffer.readLatest(golden.tubeKey)).toEqual(sample.tubePose);
    expect(source.info.getEntity(golden.tubeKey)?.values.location).toBe(sample.tubeLocation);
    expect(source.info.getEntity(golden.robotKey)?.values.jointPositions).toEqual(sample.robotJoints);
  }
  expect(source.getPlayback().playing).toBe(false);
  expect(urls.every((url) => url.startsWith('https://example.org/files/v1/'))).toBe(true);
  expect(urls).toHaveLength(4); // One cached segment and its metadata/manifest/catalog.
  expect(() => source.action(golden.robotKey, 'home', {})).toThrow(/read-only/);
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, deny) => { resolve = accept; reject = deny; });
  return { promise, resolve, reject };
}

async function delayedReplay() {
  const recording = JSON.parse(readFileSync(fixture + '/recording.json', 'utf8'));
  const original = readFileSync(fixture + '/' + recording.segments[0].file);
  const frameLength = original.readUInt32LE(6);
  const snapshotOffset = 10 + frameLength;
  const snapshotLength = original.readUInt32LE(snapshotOffset);
  const frame = original.subarray(19, 10 + frameLength);
  const snapshot = JSON.parse(original.subarray(snapshotOffset + 13, snapshotOffset + 4 + snapshotLength).toString());
  const origin = original.readDoubleLE(11);
  const files = new Map<string, Buffer>();
  const gates = new Map<string, ReturnType<typeof deferred<Response>>>();
  function record(kind: number, time: number, payload: Buffer) {
    const header = Buffer.alloc(13);
    header.writeUInt32LE(payload.length + 9, 0);
    header.writeUInt8(kind, 4);
    header.writeDoubleLE(time, 5);
    return Buffer.concat([header, payload]);
  }
  recording.segments = Array.from({ length: 3 }, (_, index) => {
    const offset = index * 2000;
    const spatial = Buffer.from(frame);
    spatial.writeBigUInt64LE(frame.readBigUInt64LE(16) + BigInt(index * 100), 16);
    spatial.writeDoubleLE(frame.readDoubleLE(32) + offset, 32);
    const info = { ...snapshot, sampleTimeMs: snapshot.sampleTimeMs + offset };
    const state = { type: 'session.state', sessionId: recording.sessionId, sceneRevision: recording.sceneRevision,
      state: 'PAUSED', epoch: 0, seq: Number(spatial.readBigUInt64LE(16)), simTime: spatial.readDoubleLE(24),
      realtimeFactor: 1, actualRtf: 0, cause: { kind: 'lifecycle' } };
    const binary = Buffer.concat([original.subarray(0, 6), record(1, origin + offset, spatial),
      record(2, info.sampleTimeMs, Buffer.from(JSON.stringify(info))),
      record(2, origin + offset + 1000, Buffer.from(JSON.stringify(state)))]);
    const file = `part-${index}.bin`;
    files.set(file, binary);
    return { ...recording.segments[0], file, startSampleTimeMs: origin + offset,
      endSampleTimeMs: origin + offset + 1000, bytes: binary.length };
  });
  recording.durationMs = 5000;
  files.set('recording.json', Buffer.from(JSON.stringify(recording)));
  vi.stubGlobal('location', { href: 'https://example.org/replay/v1' });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const name = new URL(url).pathname.split('/v1/')[1];
    if (gates.has(name)) return gates.get(name)!.promise;
    return new Response(new Uint8Array(files.get(name) ?? readFileSync(fixture + '/' + name)));
  }));
  source = new ReplaySource('v1', 'https://example.org/files/v1/recording.json');
  await source.start();
  return {
    delay(name: string) { const gate = deferred<Response>(); gates.set(name, gate); return gate; },
    response(name: string) { return new Response(new Uint8Array(files.get(name)!)); },
  };
}

it('keeps a synchronous pending target while time and poses remain at the applied sample', async () => {
  const fixture = await delayedReplay();
  const before = source.buffer.latestSample;
  const gate = fixture.delay('part-1.bin');
  const pending = source.seek(2001);
  expect(source.getPlayback()).toMatchObject({ seeking: true, seekTargetMs: 2001, timeMs: 0 });
  expect(source.buffer.latestSample).toEqual(before);
  gate.resolve(fixture.response('part-1.bin'));
  await pending;
  expect(source.getPlayback()).toMatchObject({ seeking: false, seekTargetMs: undefined, timeMs: 2001 });
  const zero = source.seek(-100);
  expect(source.getPlayback()).toMatchObject({ seeking: true, seekTargetMs: 0, timeMs: 2001 });
  await zero;
  const beyondEnd = source.seek(20000);
  expect(source.getPlayback()).toMatchObject({ seeking: true, seekTargetMs: 5000, timeMs: 0 });
  await beyondEnd;
  expect(source.getPlayback()).toMatchObject({ seeking: false, seekTargetMs: undefined, timeMs: 5000 });
});

it.each(['resolve', 'reject'] as const)('a stale %s cannot clear the newer pending target or apply a sample', async (settle) => {
  const fixture = await delayedReplay();
  const first = fixture.delay('part-1.bin'), latest = fixture.delay('part-2.bin');
  const oldSeek = source.seek(2001), newSeek = source.seek(4001);
  if (settle === 'resolve') first.resolve(fixture.response('part-1.bin'));
  else first.reject(new Error('old request failed'));
  await oldSeek;
  expect(source.getPlayback()).toMatchObject({ seeking: true, seekTargetMs: 4001, timeMs: 0 });
  expect(source.getSnapshot().error).toBeUndefined();
  latest.resolve(fixture.response('part-2.bin'));
  await newSeek;
  expect(source.getPlayback()).toMatchObject({ seeking: false, seekTargetMs: undefined, timeMs: 4001 });
});

it('a current failure clears its target and retains the last applied time and pose', async () => {
  const fixture = await delayedReplay();
  await source.seek(10);
  const before = source.buffer.latestSample;
  const gate = fixture.delay('part-1.bin');
  const pending = source.seek(2001);
  gate.reject(new Error('current request failed'));
  await expect(pending).rejects.toThrow('current request failed');
  expect(source.getPlayback()).toMatchObject({ seeking: false, seekTargetMs: undefined, timeMs: 10 });
  expect(source.buffer.latestSample).toEqual(before);
});

it.each(['resolve', 'reject'] as const)('stop clears intent and a late %s cannot revive playback', async (settle) => {
  const fixture = await delayedReplay();
  await source.seek(10);
  const before = source.buffer.latestSample;
  const gate = fixture.delay('part-1.bin');
  const pending = source.seek(2001);
  source.stop();
  expect(source.getPlayback()).toMatchObject({ ready: false, playing: false, seeking: false, seekTargetMs: undefined });
  if (settle === 'resolve') gate.resolve(fixture.response('part-1.bin'));
  else gate.reject(new Error('late request failed'));
  await pending;
  await source.seek(4001);
  expect(source.getPlayback()).toMatchObject({ ready: false, seeking: false, seekTargetMs: undefined, timeMs: 10 });
  expect(source.buffer.latestSample).toEqual(before);
});

it('a new start invalidates the previous pending intent before its metadata resolves', async () => {
  const fixture = await delayedReplay();
  await source.seek(10);
  const oldSegment = fixture.delay('part-1.bin'), metadata = fixture.delay('recording.json');
  const oldSeek = source.seek(2001);
  const restarting = source.start();
  expect(source.getPlayback()).toMatchObject({ ready: false, seeking: false, seekTargetMs: undefined, timeMs: 10 });
  oldSegment.resolve(fixture.response('part-1.bin'));
  await oldSeek;
  expect(source.getPlayback().seekTargetMs).toBeUndefined();
  metadata.resolve(fixture.response('recording.json'));
  await restarting;
  expect(source.getPlayback()).toMatchObject({ ready: true, seeking: false, seekTargetMs: undefined, timeMs: 0 });
});

it('changing the enabled playback rate during a seek does not move its applied sample or target', async () => {
  const fixture = await delayedReplay();
  const clock = vi.spyOn(performance, 'now').mockReturnValue(1000);
  source.play();
  const before = source.buffer.latestSample;
  const gate = fixture.delay('part-1.bin');
  const pending = source.seek(2001);
  clock.mockReturnValue(1025);
  source.setRate(4);
  expect(source.getPlayback()).toMatchObject({ rate: 4, timeMs: 0, seekTargetMs: 2001, seeking: true });
  expect(source.buffer.latestSample).toEqual(before);
  gate.resolve(fixture.response('part-1.bin'));
  await pending;
  expect(source.getPlayback()).toMatchObject({ rate: 4, timeMs: 2001, seekTargetMs: undefined, seeking: false });
});
