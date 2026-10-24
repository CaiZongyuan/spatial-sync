import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixture from '../../../../../packages/protocol/fixtures/manifest.json';
import type { SceneManifest, TransformFrame } from '../protocol';
import { TrainingViewClient } from './view-source';
import { TRAINING_VIEW_SCHEMA, trainingStreamIdentity, type TrainingHelloPayload } from './view-protocol';

class Socket {
  static OPEN = 1;
  static CLOSED = 3;
  static instances: Socket[] = [];
  onopen?: () => void;
  onmessage?: (event: { data: string }) => void;
  onclose?: () => void;
  closed = false;
  constructor(readonly url: string) { Socket.instances.push(this); }
  open() { this.onopen?.(); }
  emit(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }); }
  close() { this.closed = true; this.onclose?.(); }
}
const digest = 'a'.repeat(64);
const hello: TrainingHelloPayload = {
  task: 'task/test@1', taskContentHash: digest, scene: 'lab-demo@1', sceneContentHash: digest, assets: [],
  descriptorDigest: digest, mappingDigest: digest, environments: [0, 1],
  source: { ...fixture.source, kind: 'newton', newtonVersion: 'fixture-actual-version', solver: 'fixture-actual-solver', basePublishHz: 10, sampleTimeClock: 'runner-monotonic' },
  maxRateHz: 10,
};
function envelope(type: string, payload: unknown, envIndex?: number, viewId = 'view-1') {
  return { schema: TRAINING_VIEW_SCHEMA, type, jobId: 'job-1', attemptId: 'attempt-1', viewId, payload,
    ...(envIndex == null ? {} : { envIndex }) };
}
function manifest(envIndex: number, viewId = 'view-1'): SceneManifest {
  const data = { ...structuredClone(fixture) as SceneManifest, sessionId: trainingStreamIdentity(viewId, envIndex),
    sceneRevision: `training-scene-${digest}`, source: hello.source };
  for (const node of data.nodes) if (node.kind !== 'group')
    for (const visual of [...node.visuals, ...(node.fallbackVisuals ?? []), ...node.colliders])
      if (visual.kind !== 'primitive') visual.url = `/api/v1/jobs/job-1/view-assets/${visual.url.split('/').at(-1)}`;
  return data;
}
function frame(envIndex: number, x: number, seq = 5, epoch = 0, type: TransformFrame['type'] = 'stream.snapshot', viewId = 'view-1'): TransformFrame {
  return { type, protocolVersion: 2, sessionId: trainingStreamIdentity(viewId, envIndex),
    sceneRevision: `training-scene-${digest}`, epoch, seq, simTime: seq / 10, sampleTimeMs: seq * 100,
    poses: [[7, x, 2, 3, 0, 0, 0, 1]] };
}
function prepare(socket: Socket, viewId = 'view-1') {
  socket.open(); socket.emit(envelope('hello', hello, undefined, viewId));
  for (const env of hello.environments) socket.emit(envelope('manifest', manifest(env, viewId), env, viewId));
}

describe('Job training view transport', () => {
  let client: TrainingViewClient;
  const fetch = vi.fn();
  beforeEach(() => {
    vi.useFakeTimers(); vi.stubGlobal('location', new URL('https://lab.example/train/jobs/job-1'));
    vi.stubGlobal('WebSocket', Socket); vi.stubGlobal('fetch', fetch); Socket.instances = []; fetch.mockClear();
    client = new TrainingViewClient('job-1'); client.start();
  });
  afterEach(() => { client.stop(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('uses one Job socket and isolates identical keys/sequence numbers and an independent reset', () => {
    const socket = Socket.instances[0];
    expect(socket.url).toBe('wss://lab.example/api/v1/jobs/job-1/view');
    expect(Socket.instances).toHaveLength(1); prepare(socket);
    const sources = client.getSnapshot().sources, first = sources.get(0)!, second = sources.get(1)!;
    expect(first.readOnly).toBe(true); expect(first.isEntityVisible('/World/Arm')).toBe(false);
    socket.emit(envelope('frame', frame(0, 1), 0));
    expect(client.getSnapshot().state).toBe('waiting');
    socket.emit(envelope('frame', frame(1, 9), 1));
    expect(client.getSnapshot().state).toBe('live');
    expect(first.buffer.readLatest('/World/Arm')?.p).toEqual([1, 2, 3]);
    expect(second.buffer.readLatest('/World/Arm')?.p).toEqual([9, 2, 3]);
    socket.emit(envelope('frame', frame(1, 12, 6, 1), 1));
    expect(second.buffer.epoch).toBe(1); expect(second.buffer.lastSeq).toBe(6);
    expect(first.buffer.epoch).toBe(0); expect(first.buffer.lastSeq).toBe(5);
    expect(first.buffer.readLatest('/World/Arm')?.p).toEqual([1, 2, 3]);
    expect(second.buffer.readLatest('/World/Arm')?.p).toEqual([12, 2, 3]);
    expect(first.getStreamMetrics()).toMatchObject({ frameFormat: 'json', receivedFrames: 1 });
    expect(first.getStreamMetrics().payloadBytes).toBeGreaterThan(0);
    expect(() => first.action('/World/Arm', 'move', {})).toThrow('只读');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('requires a complete snapshot and does not show bodies from a normal frame first', () => {
    const socket = Socket.instances[0]; prepare(socket);
    const source = client.getSnapshot().sources.get(0)!;
    socket.emit(envelope('frame', frame(0, 1, 1, 0, 'transform.frame'), 0));
    expect(source.hasSnapshot).toBe(false); expect(source.isEntityVisible('/World/Arm')).toBe(false);
    expect(client.getSnapshot().state).toBe('waiting');
    const incomplete = { ...frame(0, 1), poses: [] };
    socket.emit(envelope('frame', incomplete, 0));
    expect(client.getSnapshot().state).toBe('error');
    expect(client.getSnapshot().error).toMatch(/缺少实际刚体/); expect(socket.closed).toBe(true);
  });

  it('preserves a same-view source, last sequence and selection owner across equal-sequence recovery', () => {
    const socket = Socket.instances[0]; prepare(socket);
    socket.emit(envelope('frame', frame(0, 1), 0)); socket.emit(envelope('frame', frame(1, 9), 1));
    const source = client.getSnapshot().sources.get(0)!;
    socket.close(); expect(source.buffer.awaitingSnapshot).toBe(true);
    expect(source.getStreamMetrics().socketReadyState).toBe(Socket.CLOSED);
    vi.advanceTimersByTime(500); const replacement = Socket.instances[1]; prepare(replacement);
    expect(client.getSnapshot().sources.get(0)).toBe(source);
    expect(source.getStreamMetrics().socketReadyState).toBe(Socket.OPEN);
    expect(source.buffer.lastSeq).toBe(5); expect(source.buffer.readLatest('/World/Arm')?.p).toEqual([1, 2, 3]);
    replacement.emit(envelope('frame', frame(0, 1), 0)); replacement.emit(envelope('frame', frame(1, 9), 1));
    expect(client.getSnapshot().state).toBe('live'); expect(source.buffer.discardedFrames).toBe(0);
    expect(source.getSnapshot().receivedFrames).toBe(2); expect(fetch).not.toHaveBeenCalled();
  });

  it('recovers from an optimizer pause using ordinary full frames on the same connection', () => {
    const socket = Socket.instances[0]; prepare(socket);
    socket.emit(envelope('frame', frame(0, 1), 0)); socket.emit(envelope('frame', frame(1, 9), 1));
    vi.advanceTimersByTime(4000);
    expect(client.getSnapshot().state).toBe('stale');
    expect(client.getSnapshot().sources.get(0)!.buffer.awaitingSnapshot).toBe(false);
    expect(client.getSnapshot().sources.get(0)!.getStreamMetrics().socketReadyState).toBe(Socket.OPEN);
    socket.emit(envelope('frame', frame(0, 2, 6, 0, 'transform.frame'), 0));
    socket.emit(envelope('frame', frame(1, 10, 6, 0, 'transform.frame'), 1));
    expect(client.getSnapshot().state).toBe('live');
    expect(client.getSnapshot().sources.get(1)!.buffer.readLatest('/World/Arm')?.p).toEqual([10, 2, 3]);
    expect(Socket.instances).toHaveLength(1);
  });

  it('disposes the old view and allows a new producer to start its sequence at zero', () => {
    const socket = Socket.instances[0]; prepare(socket);
    socket.emit(envelope('frame', frame(0, 1), 0)); const old = client.getSnapshot().sources.get(0)!;
    prepare(socket, 'view-2');
    const fresh = client.getSnapshot().sources.get(0)!;
    expect(fresh).not.toBe(old); expect(old.getSnapshot().sessionState).toBe('TRAINING_ENDED');
    socket.emit(envelope('frame', frame(0, 20, 0, 0, 'stream.snapshot', 'view-2'), 0, 'view-2'));
    socket.emit(envelope('frame', frame(1, 30, 0, 0, 'stream.snapshot', 'view-2'), 1, 'view-2'));
    expect(client.getSnapshot().state).toBe('live'); expect(fresh.buffer.lastSeq).toBe(0);
    socket.emit(envelope('frame', frame(0, 1000, 50), 0));
    expect(fresh.buffer.readLatest('/World/Arm')?.p).toEqual([20, 2, 3]);
  });

  it.each(['manifest', 'frame'])('reports a %s before hello as an ordering error', type => {
    const socket = Socket.instances[0]; socket.open();
    socket.emit(envelope(type, type === 'manifest' ? manifest(0) : frame(0, 1), 0));
    expect(client.getSnapshot()).toMatchObject({ state: 'error', error: expect.stringContaining('早于 Hello') });
    expect(client.getSnapshot().sources.size).toBe(0);
  });

  it('reports a frame before its environment manifest without manufacturing a Source', () => {
    const socket = Socket.instances[0]; socket.open(); socket.emit(envelope('hello', hello));
    socket.emit(envelope('frame', frame(0, 1), 0));
    expect(client.getSnapshot()).toMatchObject({ state: 'error', error: expect.stringContaining('早于 Manifest') });
    expect(client.getSnapshot().sources.size).toBe(0);
  });

  it('keeps waiting/status producer-free and disposes everything on a terminal message', () => {
    const socket = Socket.instances[0]; socket.open();
    socket.emit({ schema: TRAINING_VIEW_SCHEMA, type: 'status', jobId: 'job-1', payload: { state: 'waiting' } });
    expect(client.getSnapshot().sources.size).toBe(0); expect(client.getSnapshot().hello).toBeUndefined();
    prepare(socket); socket.emit(envelope('frame', frame(0, 1), 0));
    const old = client.getSnapshot().sources.get(0)!;
    socket.emit({ schema: TRAINING_VIEW_SCHEMA, type: 'ended', jobId: 'job-1', payload: { status: 'succeeded' } });
    expect(client.getSnapshot()).toMatchObject({ state: 'ended' }); expect(client.getSnapshot().sources.size).toBe(0);
    expect(old.getSnapshot().sessionState).toBe('TRAINING_ENDED'); expect(socket.closed).toBe(true);
    vi.advanceTimersByTime(30_000); expect(Socket.instances).toHaveLength(1); expect(fetch).not.toHaveBeenCalled();
  });

  it.each(['/api/v1/sessions/other/assets/model.glb', 'https://other.example/api/v1/jobs/job-1/view-assets/model.glb'])('rejects assets outside the Job route: %s', url => {
    const socket = Socket.instances[0]; socket.open(); socket.emit(envelope('hello', hello));
    const data = manifest(0), body = data.nodes.find(node => node.kind === 'body')!;
    if (body.kind !== 'body') throw new Error('fixture requires a body');
    body.visuals = [{ kind: 'glb', url, assetUpAxis: 'Y', scale: [1, 1, 1], localPose: { p: [0, 0, 0], q: [0, 0, 0, 1] } }];
    socket.emit(envelope('manifest', data, 0));
    expect(client.getSnapshot().state).toBe('error'); expect(client.getSnapshot().sources.size).toBe(0);
    expect(client.getSnapshot().error).toContain('训练素材必须使用作业自有地址');
  });
});
