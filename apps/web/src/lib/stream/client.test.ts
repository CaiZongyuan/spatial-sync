import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixture from '../../../../../packages/protocol/fixtures/manifest.json';
import type {
  Ready,
  TransformFrame,
  SessionState,
  ServerMessage,
} from '../protocol';
import { SessionClient } from './client';

class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  onopen?: () => void;
  onmessage?: (event: { data: string }) => void;
  onerror?: () => void;
  onclose?: () => void;
  constructor(readonly url: string) {
    Socket.instances.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  send(value: string) {
    this.sent.push(JSON.parse(value));
  }
  emit(message: ServerMessage) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
}

const ready: Ready = {
  type: 'server.ready',
  protocolVersion: 1,
  sessionId: fixture.sessionId,
  sceneRevision: fixture.sceneRevision,
  revisionNumericId: 1,
  epoch: 0,
  sessionState: 'RUNNING',
  simTime: 0,
  basePublishHz: 30,
  realtimeFactor: 1,
  heartbeatIntervalMs: 1000,
};
const snapshot: TransformFrame = {
  type: 'stream.snapshot',
  protocolVersion: 1,
  sessionId: fixture.sessionId,
  sceneRevision: fixture.sceneRevision,
  epoch: 0,
  seq: 5,
  simTime: 0.5,
  sampleTimeMs: 100,
  poses: [[7, 1, 2, 3, 0, 0, 0, 1]],
};
const paused: SessionState = {
  type: 'session.state',
  sessionId: fixture.sessionId,
  sceneRevision: fixture.sceneRevision,
  epoch: 0,
  seq: 5,
  simTime: 0.5,
  state: 'PAUSED',
  realtimeFactor: 1,
  actualRtf: 0,
  cause: { kind: 'command', command: 'pause' },
};
const flush = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};

describe('WebSocket lifecycle and authoritative control', () => {
  let client: SessionClient;
  let doc: EventTarget & { visibilityState: string };
  beforeEach(async () => {
    vi.useFakeTimers();
    doc = Object.assign(new EventTarget(), { visibilityState: 'visible' });
    vi.stubGlobal('document', doc);
    vi.stubGlobal('location', new URL('http://localhost:5174'));
    vi.stubGlobal('WebSocket', Socket);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () => (url.endsWith('/scene') ? fixture : {}),
      })),
    );
    Socket.instances.length = 0;
    client = new SessionClient(fixture.sessionId);
    await client.start();
    Socket.instances[0].open();
    Socket.instances[0].emit(ready);
    Socket.instances[0].emit(snapshot);
    await flush();
  });
  afterEach(() => {
    client.stop();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('subscribes only after ready and full manifest validation', () => {
    expect(Socket.instances[0].sent[0]).toMatchObject({
      type: 'client.subscribe',
      sceneRevision: fixture.sceneRevision,
      scope: 'all',
    });
    expect(client.buffer.readLatest('/World/Arm')!.p).toEqual([1, 2, 3]);
  });

  it('waits for applied ack for command success and session.state for displayed lifecycle', async () => {
    client.command('pause');
    const command = Socket.instances[0].sent.at(-1)!;
    expect(client.getSnapshot().sessionState).toBe('RUNNING');
    expect(client.getSnapshot().pending).toHaveLength(1);
    Socket.instances[0].emit({
      type: 'command.ack',
      requestId: command.requestId as string,
      status: 'applied',
      simTime: 0.5,
      epoch: 0,
    });
    await flush();
    expect(client.getSnapshot().pending).toHaveLength(0);
    expect(client.getSnapshot().commandResult).toBe('Command applied');
    expect(client.getSnapshot().sessionState).toBe('RUNNING');
    Socket.instances[0].emit(paused);
    await flush();
    expect(client.getSnapshot().sessionState).toBe('PAUSED');
  });

  it('keeps paused sessions connected on heartbeat and disconnects only on silence', async () => {
    Socket.instances[0].emit(paused);
    await flush();
    for (let i = 0; i < 5; i++) {
      Socket.instances[0].emit({
        type: 'server.heartbeat',
        state: 'PAUSED',
        simTime: 0.5,
        lastSeq: 5,
        actualRtf: 0,
        serverTimeMs: 100 + i * 1000,
      });
      await vi.advanceTimersByTimeAsync(1000);
    }
    expect(client.getSnapshot().connection).toBe('connected');
    await vi.advanceTimersByTimeAsync(2500);
    expect(client.getSnapshot().connection).toBe('disconnected');
  });

  it('recovers a paused equal-seq snapshot after reconnect', async () => {
    Socket.instances[0].emit(paused);
    await flush();
    Socket.instances[0].close();
    await vi.advanceTimersByTimeAsync(500);
    const next = Socket.instances[1];
    next.open();
    next.emit({ ...ready, sessionState: 'PAUSED' });
    next.emit(snapshot);
    await flush();
    expect(client.getSnapshot().connection).toBe('connected');
    expect(client.buffer.awaitingSnapshot).toBe(false);
    expect(client.buffer.read('/World/Arm')!.p).toEqual([1, 2, 3]);
  });

  it('requests a complete snapshot after visibility restoration and discards old periodic frames', async () => {
    doc.dispatchEvent(new Event('visibilitychange'));
    expect(client.buffer.awaitingSnapshot).toBe(true);
    Socket.instances[0].emit({ ...snapshot, type: 'transform.frame' });
    Socket.instances[0].emit(snapshot);
    await flush();
    expect(client.buffer.awaitingSnapshot).toBe(false);
    expect(client.buffer.discardedFrames).toBe(1);
  });

  it('retains unknown device state schemas as raw data and rejects stateSeq rollback', async () => {
    Socket.instances[0].emit({
      type: 'entity.state',
      sessionId: fixture.sessionId,
      sceneRevision: fixture.sceneRevision,
      entityKey: '/World',
      schema: 'future.device/99',
      stateSeq: 3,
      simTime: 0.5,
      payload: { rpm: 3000 },
    });
    Socket.instances[0].emit({
      type: 'entity.state',
      sessionId: fixture.sessionId,
      sceneRevision: fixture.sceneRevision,
      entityKey: '/World',
      schema: 'future.device/99',
      stateSeq: 2,
      simTime: 0.4,
      payload: { rpm: 0 },
    });
    await flush();
    expect(client.getSnapshot().entities['/World'].payload).toEqual({
      rpm: 3000,
    });
  });

  it('replaces the manifest and mapping before accepting the new revision snapshot', async () => {
    const nextManifest = {
      ...fixture,
      sceneRevision: 'rev-replaced',
      revisionNumericId: 2,
    };
    vi.mocked(fetch).mockImplementation(
      async () => new Response(JSON.stringify(nextManifest)),
    );
    Socket.instances[0].emit({
      type: 'scene.replaced',
      sessionId: fixture.sessionId,
      sceneRevision: 'rev-replaced',
      revisionNumericId: 2,
      epoch: 0,
    });
    await flush();
    await flush();
    Socket.instances[0].emit(paused);
    Socket.instances[0].emit(snapshot);
    Socket.instances[0].emit({
      ...snapshot,
      sceneRevision: 'rev-replaced',
      seq: 6,
    });
    await flush();
    expect(client.getSnapshot().manifest!.sceneRevision).toBe('rev-replaced');
    expect(client.getSnapshot().sessionState).toBe('LOADING');
    expect(client.buffer.latestSample!.seq).toBe(6);
    expect(Socket.instances[0].sent.at(-1)).toMatchObject({
      sceneRevision: 'rev-replaced',
    });
    expect(client.buffer.discardedFrames).toBe(1);
  });

  it('ignores an obsolete manifest response after stop/start, including StrictMode remounts', async () => {
    client.stop();
    let release!: (response: Response) => void;
    const delayed = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.mocked(fetch).mockImplementationOnce(() => delayed);
    const obsolete = client.start();
    client.stop();
    await client.start();
    const activeSocket = Socket.instances.at(-1)!;
    activeSocket.open();
    activeSocket.emit(ready);
    activeSocket.emit(snapshot);
    await flush();
    release(
      new Response(
        JSON.stringify({ ...fixture, sceneRevision: 'obsolete-inflight' }),
      ),
    );
    await obsolete;
    expect(client.getSnapshot().manifest!.sceneRevision).toBe(
      fixture.sceneRevision,
    );
    expect(client.buffer.lastSeq).toBe(5);
    expect(client.buffer.awaitingSnapshot).toBe(false);
  });
});
