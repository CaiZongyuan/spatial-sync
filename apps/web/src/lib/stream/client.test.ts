import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixture from '../../../../../packages/protocol/fixtures/manifest.json';
import binaryFixture from '../../../../../packages/protocol/fixtures/binary-frame.json';
import type {
  Ready,
  TransformFrame,
  SessionState,
  ServerMessage,
} from '../protocol';
import { SessionClient } from './client';

class Socket {
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: Socket[] = [];
  readyState = 0;
  binaryType = 'blob';
  sent: Record<string, unknown>[] = [];
  onopen?: () => void;
  onmessage?: (event: { data: string | ArrayBuffer }) => void;
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
    if (
      message.type === 'stream.snapshot' ||
      message.type === 'transform.frame'
    ) {
      const bytes = new ArrayBuffer(44 + message.poses.length * 32);
      const view = new DataView(bytes);
      view.setUint32(0, 0x4e59534e, true);
      view.setUint16(4, 1, true);
      view.setUint16(6, message.type === 'stream.snapshot' ? 2 : 1, true);
      view.setUint32(8, message.sceneRevision === 'rev-replaced' ? 2 : 1, true);
      view.setUint32(12, message.epoch, true);
      view.setBigUint64(16, BigInt(message.seq), true);
      view.setFloat64(24, message.simTime, true);
      view.setFloat64(32, message.sampleTimeMs, true);
      view.setUint32(40, message.poses.length, true);
      message.poses.forEach((pose, i) => {
        view.setUint32(44 + i * 32, pose[0], true);
        pose
          .slice(1)
          .forEach((value, j) =>
            view.setFloat32(48 + i * 32 + j * 4, value, true),
          );
      });
      this.emitBytes(bytes);
    } else this.onmessage?.({ data: JSON.stringify(message) });
  }
  emitBytes(bytes: ArrayBuffer) {
    this.onmessage?.({ data: bytes });
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
}

const ready: Ready = {
  type: 'server.ready',
  role: 'operator',
  protocolVersion: 2,
  sessionId: fixture.sessionId,
  sceneRevision: fixture.sceneRevision,
  revisionNumericId: 1,
  epoch: 0,
  sessionState: 'RUNNING',
  simTime: 0,
  basePublishHz: 30,
  realtimeFactor: 1,
  heartbeatIntervalMs: 1000,
  sampleTimeClock: 'runner-monotonic',
};
const snapshot: TransformFrame = {
  type: 'stream.snapshot',
  protocolVersion: 2,
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

async function subscribeReady(ws: Socket, message: Ready = ready) {
  ws.emit(message);
  await flush();
  acknowledge(ws);
  await flush();
}
function acknowledge(ws: Socket) {
  ws.emit({
    type: 'subscription.ack',
    requestId: ws.sent.at(-1)!.requestId as string,
    grantedRateHz: 30,
    info: true,
  });
}

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
        headers: new Headers(),
        json: async () =>
          url.endsWith('/scene')
            ? fixture
            : url.endsWith('/catalog')
              ? {
                  sceneRevision: fixture.sceneRevision,
                  types: {},
                  entities: {},
                }
              : {},
      })),
    );
    Socket.instances.length = 0;
    client = new SessionClient(fixture.sessionId);
    await client.start();
    Socket.instances[0].open();
    await subscribeReady(Socket.instances[0]);
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
      protocolVersion: 2,
      info: true,
    });
    expect(client.buffer.readLatest('/World/Arm')!.p).toEqual([1, 2, 3]);
  });

  it('receives binary frames, skips malformed input and preserves binary frame before JSON state', async () => {
    client.stop();
    client = new SessionClient(fixture.sessionId);
    await client.start();
    const ws = Socket.instances.at(-1)!;
    ws.open();
    await subscribeReady(ws);
    await flush();
    expect(ws.binaryType).toBe('arraybuffer');
    expect(ws.sent[0]).not.toHaveProperty('format');
    expect(ws.sent[0]).not.toHaveProperty('scope');
    const bytes = Uint8Array.from(binaryFixture.hex.match(/../g)!, (hex) =>
      parseInt(hex, 16),
    ).buffer;
    new DataView(bytes).setBigUint64(16, 5n, true);
    const wrong = bytes.slice(0);
    new DataView(wrong).setUint32(8, 99, true);
    ws.emitBytes(wrong);
    ws.emitBytes(bytes);
    ws.emit(paused);
    await flush();
    expect(client.buffer.readLatest('/World/Arm')!.p).toEqual([1, 2, 3]);
    expect(client.getSnapshot().sessionState).toBe('PAUSED');
    expect(client.getStreamMetrics().invalidBinaryFrames).toBe(1);
    expect(client.getStreamMetrics().receivedFrames).toBe(1);
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

  it('reports an unsent command when the socket is closing before the view receives close', async () => {
    const socket = Socket.instances[0];
    expect(client.getSnapshot().connection).toBe('connected');
    socket.readyState = Socket.CLOSING;
    const sent = socket.sent.length;
    client.command('step', { count: 1 });
    expect(client.getSnapshot().commandResult).toBe(
      'Connection unavailable. Retry after reconnect.',
    );
    expect(client.getSnapshot().pending).toEqual([]);
    expect(socket.sent).toHaveLength(sent);
    socket.close();
    await vi.advanceTimersByTimeAsync(500);
    const next = Socket.instances.at(-1)!;
    next.open();
    await subscribeReady(next);
    next.emit(snapshot);
    await flush();
    expect(
      next.sent.some((message) => message.type === 'command.request'),
    ).toBe(false);
    expect(client.getSnapshot().pending).toEqual([]);
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
    await subscribeReady(next, { ...ready, sessionState: 'PAUSED' });
    next.emit(snapshot);
    await flush();
    expect(client.getSnapshot().connection).toBe('connected');
    expect(client.buffer.awaitingSnapshot).toBe(false);
    expect(client.buffer.read('/World/Arm')!.p).toEqual([1, 2, 3]);
  });

  it('requests a complete snapshot after visibility restoration and discards old periodic frames', async () => {
    doc.dispatchEvent(new Event('visibilitychange'));
    expect(client.buffer.awaitingSnapshot).toBe(true);
    acknowledge(Socket.instances[0]);
    Socket.instances[0].emit({ ...snapshot, type: 'transform.frame' });
    Socket.instances[0].emit(snapshot);
    await flush();
    expect(client.buffer.awaitingSnapshot).toBe(false);
    expect(client.buffer.discardedFrames).toBe(1);
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
    acknowledge(Socket.instances[0]);
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
    expect(client.buffer.invalidBinaryFrames).toBe(1);
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
    await subscribeReady(activeSocket);
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
  it('uses the same information handler for snapshots and action outcomes with authorized identity', async () => {
    const ws = Socket.instances[0];
    ws.emit({
      type: 'entity.snapshot',
      sceneRevision: fixture.sceneRevision,
      epoch: 0,
      simTime: 0,
      sampleTimeMs: 0,
      entities: [
        { key: '/machine', rev: 1, values: { phase: 'idle' }, meta: {} },
      ],
      invocations: [],
    });
    await flush();
    const requestId = client.action('/machine', 'closeLid', {});
    expect(ws.sent.at(-1)).toEqual({
      type: 'action.request',
      requestId,
      entityKey: '/machine',
      action: 'closeLid',
      params: {},
    });
    ws.emit({
      type: 'invocation.update',
      sceneRevision: fixture.sceneRevision,
      invocationId: 'inv-1',
      requestId,
      entityKey: '/machine',
      action: 'closeLid',
      params: {},
      actor: { kind: 'user', name: 'web' },
      epoch: 0,
      createdSimTime: 0,
      createdAtMs: 1,
      status: 'succeeded',
    });
    await flush();
    expect(client.info.getEntity('/machine')?.values.phase).toBe('idle');
    expect(client.info.getInvocations('/machine')[0].status).toBe('succeeded');
    expect(client.getSnapshot().role).toBe('operator');
  });
  it('revalidates the Catalog with its ETag after reconnect and retains the matching definition', async () => {
    client.stop();
    const catalog = {
      sceneRevision: fixture.sceneRevision,
      types: {},
      entities: {},
    };
    let catalogRequests = 0;
    vi.mocked(fetch).mockImplementation(async (url) => {
      if (String(url).endsWith('/scene'))
        return new Response(JSON.stringify(fixture));
      if (String(url).endsWith('/catalog')) {
        catalogRequests++;
        return catalogRequests === 1
          ? new Response(JSON.stringify(catalog), {
              headers: { ETag: '"catalog-a"' },
            })
          : new Response(null, { status: 304 });
      }
      return new Response('{}');
    });
    client = new SessionClient(fixture.sessionId);
    await client.start();
    const first = Socket.instances.at(-1)!;
    first.open();
    await subscribeReady(first);
    await flush();
    expect(client.info.getMetadata().catalog).toEqual(catalog);
    first.close();
    await vi.advanceTimersByTimeAsync(500);
    const second = Socket.instances.at(-1)!;
    second.open();
    await subscribeReady(second);
    await flush();
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/sessions/${fixture.sessionId}/catalog`,
      { headers: { 'If-None-Match': '"catalog-a"' } },
    );
    expect(client.info.getMetadata().catalog).toEqual(catalog);
  });

  it('keeps viewer action requests read only even after synchronized state arrives', async () => {
    const ws = Socket.instances[0];
    await subscribeReady(ws, { ...ready, role: 'viewer' });
    ws.emit({
      type: 'entity.snapshot',
      sceneRevision: fixture.sceneRevision,
      epoch: 0,
      simTime: 0,
      sampleTimeMs: 0,
      entities: [],
      invocations: [],
    });
    await flush();
    expect(() => client.action('/machine', 'start', {})).toThrow(
      'Operator access',
    );
    expect(ws.sent.some((message) => message.type === 'action.request')).toBe(
      false,
    );
  });
});
