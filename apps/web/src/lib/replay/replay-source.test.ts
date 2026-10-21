import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, expect, it, vi } from 'vitest';
import { ReplaySource } from './replay-source';

const fixture = fileURLToPath(new URL('../../../../../tests/fixtures/recordings/v1/', import.meta.url));
let source: ReplaySource;

afterEach(() => {
  source?.stop();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('replays sequentially when an optional property index attachment is unavailable', async () => {
  const fixture = fileURLToPath(new URL('../../../../../tests/fixtures/recordings/v2/', import.meta.url));
  const metadata = JSON.parse(readFileSync(fixture + '/recording.json', 'utf8'));
  metadata.segments[0].propertyIndex = 'seg-000001.index.json';
  const requested: string[] = [];
  vi.stubGlobal('location', { href: 'https://example.org/replay/v2' });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const name = new URL(url).pathname.split('/v2/')[1];
    requested.push(name);
    if (name.endsWith('.index.json')) return new Response(null, { status: 404 });
    return name === 'recording.json' ? Response.json(metadata)
      : new Response(new Uint8Array(readFileSync(fixture + '/' + name)));
  }));
  source = new ReplaySource('v2', 'https://example.org/files/v2/recording.json');
  await source.start();
  await source.seek(source.getPlayback().durationMs);
  expect(source.getPlayback()).toMatchObject({ ready: true, sceneVersion: 'lab-demo@2' });
  expect(requested).not.toContain('seg-000001.index.json');
});

it.each([1, 2])('reconstructs independent states and poses from the published v%i fixture', async (version) => {
  const fixture = fileURLToPath(new URL(`../../../../../tests/fixtures/recordings/v${version}/`, import.meta.url));
  const golden = JSON.parse(readFileSync(fixture + '/expected.json', 'utf8')) as {
    tubeKey: string; robotKey: string;
    samples: { timeMs: number; seq: number; tubePose: { p: number[]; q: number[] };
      tubeLocation: string; robotJoints: number[] }[];
  };
  const urls: string[] = [];
  vi.stubGlobal('location', { href: `https://example.org/replay/v${version}` });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    const name = new URL(url).pathname.split(`/v${version}/`)[1];
    return new Response(new Uint8Array(readFileSync(fixture + '/' + name)));
  }));
  source = new ReplaySource(`v${version}`, `https://example.org/files/v${version}/recording.json`);
  await source.start();
  expect(source.readOnly).toBe(true);
  expect(source.getPlayback()).toMatchObject({ sceneVersion: `lab-demo@${version}`, kind: 'session' });
  if (version === 1) expect(source.getPlayback().sceneContentHash).toBeNull();
  expect(source.info.getMetadata().ready).toBe(true);
  for (const sample of golden.samples) {
    await source.seek(sample.timeMs);
    expect(source.buffer.latestSample?.seq).toBe(sample.seq);
    expect(source.buffer.readLatest(golden.tubeKey)).toEqual(sample.tubePose);
    expect(source.info.getEntity(golden.tubeKey)?.values.location).toBe(sample.tubeLocation);
    expect(source.info.getEntity(golden.robotKey)?.values.jointPositions).toEqual(sample.robotJoints);
  }
  expect(source.getPlayback().playing).toBe(false);
  expect(urls.every((url) => url.startsWith(`https://example.org/files/v${version}/`))).toBe(true);
  expect(urls).toHaveLength(4); // One cached segment and its metadata/manifest/catalog.
  expect(() => source.action(golden.robotKey, 'home', {})).toThrow(/read-only/);
});

it('preserves nonzero height, physical TCP and tube bottom from the public writer archive', async () => {
  const directory = fileURLToPath(new URL('../../../../../tests/fixtures/recordings/v2-grasp-height/', import.meta.url));
  const golden = JSON.parse(readFileSync(directory + '/expected.json', 'utf8'));
  const urls: string[] = [];
  vi.stubGlobal('location', { href: 'https://example.org/replay/grasp-height' });
  const webSocket = vi.fn();
  vi.stubGlobal('WebSocket', webSocket);
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    const name = new URL(url).pathname.split('/grasp-height/')[1];
    return new Response(new Uint8Array(readFileSync(directory + '/' + name)));
  }));
  source = new ReplaySource('grasp-height', 'https://example.org/files/grasp-height/recording.json');
  await source.start();
  expect(source.info.getMetadata().catalog?.entities[golden.robotKey].config.graspHeightM).toBe(.020);
  for (const sample of golden.samples) {
    await source.seek(sample.timeMs);
    expect(source.buffer.latestSample?.seq).toBe(sample.seq);
    expect(source.buffer.readLatest(golden.tubeKey)).toEqual(sample.tubePose);
    const values = source.info.getEntity(golden.robotKey)?.values;
    expect(values?.tcpPose).toEqual(sample.tcpPose);
    expect(values?.jointPositions).toEqual(sample.robotJoints);
    expect((values?.tcpPose as number[])[2] - sample.tubePose.p[2]).toBeCloseTo(.020, 6);
  }
  expect(webSocket).not.toHaveBeenCalled();
  expect(urls.every(url => url.startsWith('https://example.org/files/grasp-height/'))).toBe(true);
  expect(() => source.action(golden.robotKey, 'pick', { location: 'rack.A1' })).toThrow(/read-only/);
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, deny) => { resolve = accept; reject = deny; });
  return { promise, resolve, reject };
}

async function delayedReplay(freshness = false) {
  const recording = JSON.parse(readFileSync(fixture + '/recording.json', 'utf8'));
  if (freshness) Object.assign(recording, { formatVersion: 2, protocolMinorVersion: 1,
    sceneVersion: 'lab-demo@1', sceneContentHash: 'a'.repeat(64), kind: 'session' });
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
    const info = structuredClone({ ...snapshot, sampleTimeMs: snapshot.sampleTimeMs + offset });
    const tube = info.entities.find((entity: { key: string }) => entity.key === '/World/Lab/Tubes/Tube01');
    if (freshness && index === 1) tube.meta.location.stale = true;
    const state = { type: 'session.state', sessionId: recording.sessionId, sceneRevision: recording.sceneRevision,
      state: 'PAUSED', epoch: 0, seq: Number(spatial.readBigUInt64LE(16)), simTime: spatial.readDoubleLE(24),
      realtimeFactor: 1, actualRtf: 0, cause: { kind: 'lifecycle' } };
    const binary = Buffer.concat([original.subarray(0, 6), record(1, origin + offset, spatial),
      record(2, info.sampleTimeMs, Buffer.from(JSON.stringify(info))),
      ...(freshness && index === 0 ? [record(2, origin + 500, Buffer.from(JSON.stringify({
        type: 'entity.observations', sceneRevision: recording.sceneRevision, epoch: 0,
        simTime: 0.5, sampleTimeMs: origin + 500, source: 'virtual',
        changes: [{ key: tube.key, rev: tube.rev + 1, values: { location: tube.values.location },
          meta: { location: { ...tube.meta.location, stale: true } } }],
      })))] : []),
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

it('preserves 2.1 freshness on observations, segment-prefix seek, rewind and fresh recovery', async () => {
  await delayedReplay(true);
  const entity = () => source.info.getEntity('/World/Lab/Tubes/Tube01')!;
  const scalar = entity().values.location;
  expect(entity().meta.location.stale).not.toBe(true);
  await source.seek(500);
  expect(entity().values.location).toBe(scalar);
  expect(entity().meta.location.stale).toBe(true);
  await source.seek(2001);
  expect(entity().meta.location.stale).toBe(true);
  await source.seek(4001);
  expect(entity().meta.location.stale).not.toBe(true);
  await source.seek(0);
  expect(entity().meta.location.stale).not.toBe(true);
  await source.seek(2001);
  expect(entity().values.location).toBe(scalar);
  expect(entity().meta.location.stale).toBe(true);
});

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
