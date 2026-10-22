import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, expect, it, vi } from 'vitest';
import { LiveClient, type LiveStatus } from './live-client';
import type { Catalog } from './protocol';

const fixture = fileURLToPath(new URL('../../../../tests/fixtures/recordings/v2-live/', import.meta.url));
const key = '/Real/Incubator2';
let client: LiveClient;

afterEach(() => {
  client?.stop();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

function transport(role: 'operator' | 'viewer' = 'operator') {
  vi.useFakeTimers();
  let status: LiveStatus = { id: 'live-first', name: 'lab', state: 'RUNNING', sceneRevision: 'live-first-revision',
    epoch: 0, simTime: 1, sampleTimeMs: 620_100, eventSeq: 5, role, broker: 'mqtt://localhost:1883',
    layout: null, layoutContentHash: null, layoutError: null, placements: {}, rules: {},
    recording: { id: 'live-lab', status: 'recording', retentionDays: 7 } };
  const calls: { path: string; options?: RequestInit }[] = [];
  const batches: ReturnType<typeof deferred<Response>>[] = [];
  const action = deferred<Response>();
  let complete = false;
  let failedSnapshot = false;
  let temperature = 37;
  vi.stubGlobal('fetch', vi.fn(async (input: string, options?: RequestInit) => {
    const path = input.replace('/api/v1', '');
    calls.push({ path, options });
    if (path === '/live') return Response.json(status);
    if (path === '/live/catalog') {
      const catalog: Catalog = JSON.parse(readFileSync(fixture + '/catalog.json', 'utf8'));
      return Response.json({ ...catalog, sceneRevision: status.sceneRevision });
    }
    if (path === '/live/snapshot') {
      if (failedSnapshot) { failedSnapshot = false; return Response.json({ detail: 'Temporary failure' }, { status: 503 }); }
      return Response.json({ cursor: status.eventSeq,
      snapshot: { type: 'entity.snapshot', sceneRevision: status.sceneRevision, epoch: status.epoch,
        simTime: status.simTime, sampleTimeMs: status.sampleTimeMs, invocations: [], entities: [{
          key, rev: 1, values: { temperatureC: status.id === 'live-first' ? temperature : 39 },
          meta: { temperatureC: { simTime: 0, sampleTimeMs: status.sampleTimeMs - 100, source: 'device' } },
        }] },
      plans: [{ type: 'plan.update', update: 'full', sceneRevision: status.sceneRevision, epoch: status.epoch,
        simTime: status.simTime, sampleTimeMs: status.sampleTimeMs, plan: {
          id: 'plan-existing', kind: 'operation', title: 'Existing real plan', target: 'live', revision: 1,
          status: complete ? 'completed' : 'running', steps: [{ id: 1, status: complete ? 'done' : 'running',
            do: { entity: key, action: 'setSetpoint', params: { temperatureC: 38 } } }],
        } }],
      });
    }
    if (path.startsWith('/live/events?')) {
      const batch = deferred<Response>(); batches.push(batch);
      options?.signal?.addEventListener('abort', () => batch.resolve(Response.json({ events: [], lastSeq: 0, gap: false })),
        { once: true });
      return batch.promise;
    }
    if (path === '/live/actions') return action.promise;
    throw new Error(`Unexpected request: ${path}`);
  }));
  return { calls, batches, action, get status() { return status; }, setStatus(value: LiveStatus) { status = value; },
    completePlan() { complete = true; }, failSnapshotOnce() { failedSnapshot = true; },
    setTemperature(value: number) { temperature = value; } };
}

it('retries a failed snapshot on the same Host before accepting more events', async () => {
  const server = transport();
  client = new LiveClient();
  await client.start();
  expect(client.info.getMetadata().ready).toBe(true);
  server.failSnapshotOnce();
  await client.start();
  expect(client.info.getMetadata().ready).toBe(false);
  expect(client.getSnapshot().connection).toBe('disconnected');
  server.setTemperature(41);
  server.completePlan();
  server.setStatus({ ...server.status, eventSeq: 20 });
  await vi.advanceTimersByTimeAsync(1000);
  expect(client.info.getMetadata().ready).toBe(true);
  expect(client.getLiveStatus()?.id).toBe('live-first');
  expect(client.info.getEntity(key)?.values.temperatureC).toBe(41);
  expect(client.plans.getSnapshot().entries[0].plan.status).toBe('completed');
  expect(server.calls.filter(call => call.path === '/live/snapshot')).toHaveLength(3);
  expect(server.calls.some(call => call.path.startsWith('/live/events?after=20&'))).toBe(true);
});

it('bootstraps actual public information fields and sends generic HTTP actions without spatial requests', async () => {
  const server = transport();
  const webSocket = vi.fn(); vi.stubGlobal('WebSocket', webSocket);
  client = new LiveClient();
  await client.start();
  expect(client.info.getEntity(key)?.meta.temperatureC.sampleTimeMs).toBe(620_000);
  expect(client.getSnapshot()).toMatchObject({ connection: 'connected', role: 'operator', simTime: 1 });
  expect(client.getSnapshot().manifest).toBeUndefined();
  const requestId = client.action(key, 'setSetpoint', { temperatureC: 38 });
  const request = server.calls.find(call => call.path === '/live/actions')!;
  expect(JSON.parse(request.options!.body as string)).toEqual({ requestId, entityKey: key,
    action: 'setSetpoint', params: { temperatureC: 38 } });
  expect(request.options?.headers).toEqual({ 'Content-Type': 'application/json', 'X-Actor': 'user:web' });
  expect(webSocket).not.toHaveBeenCalled();
  expect(server.calls.every(call => call.path.startsWith('/live'))).toBe(true);
  expect(() => client.command()).toThrow(/设备动作/);
});

it('keeps viewer controls out of the action pipeline', async () => {
  const server = transport('viewer');
  client = new LiveClient();
  await client.start();
  expect(() => client.action(key, 'setSetpoint', { temperatureC: 38 })).toThrow(/Operator/);
  expect(() => client.approveInvocation('inv-any', 'approve')).toThrow(/Operator/);
  expect(() => client.cancelInvocation('inv-any')).toThrow(/Operator/);
  expect(server.calls.some(call => call.options?.method === 'POST')).toBe(false);
});

it('resynchronizes an event gap to the new Host and ignores a delayed old action result', async () => {
  const server = transport();
  client = new LiveClient();
  await client.start();
  client.action(key, 'setSetpoint', { temperatureC: 38 });
  await vi.advanceTimersByTimeAsync(0);
  expect(server.batches).toHaveLength(1);
  server.setStatus({ ...server.status, id: 'live-second', sceneRevision: 'live-second-revision', eventSeq: 0,
    sampleTimeMs: 740_100, simTime: 0 });
  server.batches[0].resolve(Response.json({ events: [], lastSeq: 0, gap: true }));
  await vi.waitFor(() => expect(client.getLiveStatus()?.id).toBe('live-second'));
  expect(client.info.getEntity(key)?.values.temperatureC).toBe(39);
  server.action.resolve(Response.json({ invocationId: 'inv-old', requestId: 'old', entityKey: key,
    action: 'setSetpoint', params: { temperatureC: 38 }, actor: { kind: 'user', name: 'web' }, epoch: 0,
    createdSimTime: .1, createdAtMs: 1_700_000_000_000, status: 'accepted' }));
  await vi.advanceTimersByTimeAsync(0);
  expect(client.info.getInvocations(key)).toEqual([]);
  expect(server.calls.filter(call => call.options?.method === 'POST')).toHaveLength(1);
  expect(server.calls.some(call => call.path.includes('/sessions/'))).toBe(false);
});

it('restores a preexisting complete plan snapshot before continuing from its exact event cursor', async () => {
  const server = transport();
  client = new LiveClient();
  await client.start();
  expect(client.plans.getSnapshot().entries[0].plan).toMatchObject({ id: 'plan-existing', status: 'running' });
  await vi.advanceTimersByTimeAsync(0);
  server.completePlan();
  server.setStatus({ ...server.status, eventSeq: 20 });
  server.batches[0].resolve(Response.json({ events: [], lastSeq: 20, gap: true }));
  await vi.waitFor(() => expect(client.plans.getSnapshot().entries[0].plan).toMatchObject({
    id: 'plan-existing', status: 'completed', steps: [{ id: 1, status: 'done' }],
  }));
  await vi.advanceTimersByTimeAsync(0);
  expect(server.calls.some(call => call.path.startsWith('/live/events?after=20&'))).toBe(true);
  expect(server.calls.some(call => call.options?.method === 'POST')).toBe(false);
});
