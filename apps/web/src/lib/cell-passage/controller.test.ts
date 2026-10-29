import { describe, expect, it, vi } from 'vitest';
import { createServer } from 'node:http';
import { PassageController } from './controller';
import type { Catalog } from '../protocol';
import type { PlanDocument } from '../plans/model';

function fixture() {
  const session = { sessionId: 's', sceneRevision: 'r', epoch: 0, state: 'RUNNING', simTime: 1 };
  const values = { controlOwner: '', controlEpoch: 3, controlMode: 'auto', controlSafe: true, materialRevision: 4,
    auditPassed: true, materialSnapshot: { revision: 4, reconciled: true, tips: [], tip: { id: '', volumeUl: 0, components: [], cells: [] },
      vessels: [{ id: 'source', entityKey: '/w', culture: '', parent: '', capacityUl: 1000, volumeUl: 500, components: [{ name: 'medium', volumeUl: 500 }], cells: [] }], reservations: [], observations: [], operations: [], controlRequests: [] as unknown[] } };
  const plan = { id: 'p', kind: 'operation', target: 'session', sessionId: 's', sceneRevision: 'r', epoch: 0, revision: 1,
    status: 'approved', steps: [{ id: 200001, status: 'pending', do: { entity: '/w', action: 'aspirate', params: { owner: '', epoch: 3, operationId: 'liquid-1', vessel: 'mother', volumeUl: 200 } } }], stepResults: {} } as unknown as PlanDocument;
  let invocation = { invocationId: 'i', status: 'running' };
  let loseResponse = false;
  const controlInvocations = new Map<string, Record<string, unknown>>();
  const safeMeta = { simTime: 1, sampleTimeMs: 100, source: 'virtual' as const };
  const http = vi.fn(async (path: string, options?: RequestInit): Promise<unknown> => {
    if (path === '/sessions/s') return structuredClone(session);
    if (path === '/plans/p') return structuredClone(plan);
    if (path.includes('/state?')) return { entities: { '/w': { key: '/w', rev: 1, values: structuredClone(values), meta: { controlSafe: structuredClone(safeMeta) } } } };
    if (path.includes('/invocations?')) return { invocations: [] };
    if (path.endsWith('/check')) return { satisfied: true };
    if (path.endsWith('/revisions')) return structuredClone(plan);
    if (path === '/sessions/s/actions') {
      if (loseResponse) throw new TypeError('network response lost');
      const body = JSON.parse(String(options?.body));
      if (['requestControl', 'confirmControl'].includes(body.action)) {
        const row = { ...body, invocationId: `control-${controlInvocations.size}`, epoch: session.epoch, status: 'succeeded', startedSimTime: session.simTime };
        controlInvocations.set(row.invocationId, row); safeMeta.sampleTimeMs++;
        return structuredClone(row);
      }
      return structuredClone(invocation);
    }
    if (path.includes('/invocations/')) return structuredClone(controlInvocations.get(path.split('/').at(-1)!) ?? invocation);
    throw new Error(`Unexpected request ${path}`);
  });
  const controller = new PassageController('s', '/w', http as never);
  values.controlOwner = controller.owner;
  if ('do' in plan.steps[0]) plan.steps[0].do.params.owner = controller.owner;
  return { controller, session, values, plan, http, safeMeta, controlInvocations, setInvocation: (status: string) => { invocation = { ...invocation, status }; }, lose: () => { loseResponse = true; } };
}
const catalog = { sceneRevision: 'r', entities: { '/w': { type: 'cell-passage-workcell/v1', config: { managedDevices: ['/w'], tips: [], vessels: [{ id: 'source', entityKey: '/w', site: '/sourceSite', capacityUl: 1000, volumeUl: 500, reagent: 'medium' }] } } }, types: { 'cell-passage-workcell/v1': { properties: { controlSafe: {} } } } } as unknown as Catalog;
async function arm(f: ReturnType<typeof fixture>) { const review = await f.controller.review('p', catalog); await f.controller.prepareRemaining('p', review); await f.controller.arm('p'); }
const actionCalls = (f: ReturnType<typeof fixture>) => f.http.mock.calls.filter(([path]) => path.endsWith('/actions'));
describe('passage browser scheduling through existing HTTP/Plan API', () => {
  it('waits for Source start and a fresh controlSafe property sample, not the global clock', async () => {
    const f = fixture(); f.values.controlOwner = 'previous';
    await f.controller.begin('p', catalog, 'manual');
    const body = JSON.parse(String(actionCalls(f)[0][1]?.body));
    const invocation = [...f.controlInvocations.values()][0]; invocation.status = 'accepted'; invocation.startedSimTime = null;
    f.values.controlEpoch = 4; f.values.controlMode = 'holding';
    f.values.materialSnapshot.controlRequests.push({ operationId: body.params.operationId, action: 'requestControl', epoch: 4,
      owner: 'previous', mode: 'holding', paramsJson: JSON.stringify(body.params) });
    await f.controller.progress(); expect(actionCalls(f)).toHaveLength(1);
    invocation.status = 'running'; invocation.startedSimTime = 2;
    f.session.simTime = 10;
    await f.controller.progress(); expect(actionCalls(f)).toHaveLength(1);
    f.safeMeta.simTime = 2; f.safeMeta.sampleTimeMs++;
    await f.controller.progress(); expect(actionCalls(f)).toHaveLength(2);
  });
  it('retries definitive never-started not_safe only after new physical samples with bounded new request identities', async () => {
    const f = fixture(); f.values.controlOwner = 'previous';
    await f.controller.begin('p', catalog, 'manual');
    const body = JSON.parse(String(actionCalls(f)[0][1]?.body));
    f.values.controlEpoch = 4; f.values.controlMode = 'holding';
    f.values.materialSnapshot.controlRequests.push({ operationId: body.params.operationId, action: 'requestControl', epoch: 4,
      owner: 'previous', mode: 'holding', paramsJson: JSON.stringify(body.params) });
    await f.controller.progress();
    const confirm = [...f.controlInvocations.values()][1];
    Object.assign(confirm, { status: 'rejected', startedSimTime: null, error: { code: 'not_safe' } });
    f.safeMeta.sampleTimeMs--; // Posting is not itself a new physical sample.
    for (let n = 0; n < 3; n++) await f.controller.progress();
    expect(actionCalls(f)).toHaveLength(2);
    f.safeMeta.sampleTimeMs += 2; await f.controller.progress();
    expect(actionCalls(f)).toHaveLength(3);
    const first = JSON.parse(String(actionCalls(f)[1][1]?.body));
    const second = JSON.parse(String(actionCalls(f)[2][1]?.body));
    expect(second.params).toEqual(first.params); expect(second.requestId).not.toBe(first.requestId);
    for (let attempt = 0; attempt < 2; attempt++) {
      const current = [...f.controlInvocations.values()].at(-1)!;
      Object.assign(current, { status: 'rejected', startedSimTime: null, error: { code: 'not_safe' } });
      await f.controller.progress();
      f.safeMeta.sampleTimeMs++;
      if (attempt === 0) await f.controller.progress();
      else await expect(f.controller.progress()).rejects.toThrow('上限');
    }
    expect(actionCalls(f)).toHaveLength(4); expect(f.controller.flow?.phase).toBe('blocked');
  });
  it('recovers a discarded HTTP 409 invocation by GET before classifying a definitive rejection', async () => {
    const f = fixture(); f.values.controlOwner = 'previous';
    let rejectConfirm = true;
    const original = f.http.getMockImplementation()!;
    f.http.mockImplementation(async (path, options) => {
      if (path.includes('/invocations?entity=')) return { invocations: [...f.controlInvocations.values()] };
      const result = await original(path, options);
      if (path.endsWith('/actions') && JSON.parse(String(options?.body)).action === 'confirmControl' && rejectConfirm) {
        rejectConfirm = false;
        const row = [...f.controlInvocations.values()].at(-1)!;
        Object.assign(row, { status: 'rejected', startedSimTime: null, error: { code: 'not_safe' } });
        f.safeMeta.sampleTimeMs--;
        throw new Error('HTTP 409 top-level Invocation discarded by request()');
      }
      return result;
    });
    await f.controller.begin('p', catalog, 'manual');
    const body = JSON.parse(String(actionCalls(f)[0][1]?.body));
    f.values.controlEpoch = 4; f.values.controlMode = 'holding';
    f.values.materialSnapshot.controlRequests.push({ operationId: body.params.operationId, action: 'requestControl', epoch: 4,
      owner: 'previous', mode: 'holding', paramsJson: JSON.stringify(body.params) });
    await f.controller.progress(); await f.controller.progress();
    expect(actionCalls(f)).toHaveLength(2);
    f.safeMeta.sampleTimeMs++; await f.controller.progress();
    expect(actionCalls(f)).toHaveLength(3);
  });
  it('requires every managed controlSafe sample to be fresh before confirmation', async () => {
    const f = fixture(); f.values.controlOwner = 'previous';
    const c = structuredClone(catalog); c.entities['/w'].config.managedDevices = ['/managed'];
    const managed = { key: '/managed', values: { controlSafe: true }, meta: { controlSafe: { simTime: 0, sampleTimeMs: 10, source: 'virtual' } } };
    const original = f.http.getMockImplementation()!;
    f.http.mockImplementation(async (path, options) => {
      const result = await original(path, options);
      if (path.includes('/state?')) (result as { entities: Record<string, unknown> }).entities['/managed'] = structuredClone(managed);
      return result;
    });
    await f.controller.begin('p', c, 'manual');
    const body = JSON.parse(String(actionCalls(f)[0][1]?.body));
    f.values.controlEpoch = 4; f.values.controlMode = 'holding';
    f.values.materialSnapshot.controlRequests.push({ operationId: body.params.operationId, action: 'requestControl', epoch: 4,
      owner: 'previous', mode: 'holding', paramsJson: JSON.stringify(body.params) });
    await f.controller.progress(); expect(actionCalls(f)).toHaveLength(1);
    managed.meta.controlSafe.simTime = 1; managed.meta.controlSafe.sampleTimeMs++;
    await f.controller.progress(); expect(actionCalls(f)).toHaveLength(2);
  });
  it('does not repost across a real HTTP ACK followed by delayed state publication', async () => {
    const f = fixture(); f.values.controlOwner = 'previous';
    const server = createServer(async (req, res) => {
      let body = ''; for await (const chunk of req) body += chunk;
      try { const result = await f.http(req.url!, body ? { method: req.method, body } : undefined);
        res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(result));
      } catch { res.statusCode = 500; res.end('{}'); }
    });
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
    const address = server.address(); if (!address || typeof address === 'string') throw new Error('HTTP fixture unavailable');
    const actualTransport = async <T,>(path: string, options?: RequestInit): Promise<T> => {
      const response = await fetch(`http://127.0.0.1:${address.port}${path}`, options);
      if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json() as Promise<T>;
    };
    const controller = new PassageController('s', '/w', actualTransport);
    try {
      await controller.begin('p', catalog, 'manual');
      for (let n = 0; n < 4; n++) await controller.progress();
      expect(actionCalls(f)).toHaveLength(1); expect(controller.flow?.phase).toBe('acquiring');
      const body = JSON.parse(String(actionCalls(f)[0][1]?.body));
      f.values.controlEpoch = 4; f.values.controlMode = 'holding';
      f.values.materialSnapshot.controlRequests.push({ operationId: body.params.operationId, action: 'requestControl',
        epoch: 4, owner: 'previous', mode: 'holding', paramsJson: JSON.stringify(body.params) });
      await controller.progress();
      for (let n = 0; n < 4; n++) await controller.progress();
      expect(actionCalls(f)).toHaveLength(2);
    } finally { server.closeAllConnections(); await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); }
  });
  it.each(['owner', 'epoch', 'scene', 'session'] as const)('stops an outstanding handoff when %s conflicts', async change => {
    const f = fixture(); f.values.controlOwner = 'previous';
    await f.controller.begin('p', catalog, 'manual');
    if (change === 'owner') f.values.controlOwner = 'competitor';
    if (change === 'epoch') f.values.controlEpoch = 5;
    if (change === 'scene') f.session.sceneRevision = 'changed';
    if (change === 'session') f.session.epoch = 1;
    await expect(f.controller.progress()).rejects.toThrow();
    expect(actionCalls(f)).toHaveLength(1); expect(f.controller.dispatching).toBe(false);
  });
  it('never retries an unknown admission and only GETs while the baseline remains cached', async () => {
    const f = fixture(); f.values.controlOwner = 'previous'; f.lose();
    await f.controller.begin('p', catalog, 'manual');
    for (let n = 0; n < 5; n++) await f.controller.progress();
    expect(actionCalls(f)).toHaveLength(1); expect(f.controller.flow?.phase).toBe('acquiring');
    f.values.controlEpoch = 4; f.values.controlMode = 'holding';
    for (let n = 0; n < 3; n++) await f.controller.progress();
    expect(actionCalls(f)).toHaveLength(1); // Missing receipt is not our handoff.
  });
  it('keeps a paused physical hold and pending calls ahead of confirmation', async () => {
    const f = fixture(); f.values.controlOwner = 'previous';
    await f.controller.begin('p', catalog, 'manual');
    const body = JSON.parse(String(actionCalls(f)[0][1]?.body));
    f.values.controlEpoch = 4; f.values.controlMode = 'holding'; f.values.controlSafe = false; f.session.state = 'PAUSED';
    f.values.materialSnapshot.controlRequests.push({ action: 'requestControl', operationId: body.params.operationId,
      epoch: 4, owner: 'previous', mode: 'holding', paramsJson: JSON.stringify(body.params) });
    await f.controller.progress(); expect(f.controller.flow?.phase).toBe('paused-hold');
    expect(actionCalls(f)).toHaveLength(1);
    const original = f.http.getMockImplementation()!;
    f.http.mockImplementation(async (path, options) => path.endsWith('/commands') ? {} : original(path, options));
    await f.controller.continueFlow(); expect(f.controller.flow?.phase).toBe('acquiring');
    expect(actionCalls(f)).toHaveLength(1); expect(f.controller.dispatching).toBe(false);
  });
  it('observes delayed handoff and confirmation witnesses without repeating either POST', async () => {
    const f = fixture(); f.values.controlOwner = 'previous';
    await f.controller.begin('p', catalog, 'manual');
    for (let n = 0; n < 4; n++) await f.controller.progress();
    expect(actionCalls(f)).toHaveLength(1);
    const request = JSON.parse(String(actionCalls(f)[0][1]?.body));
    f.values.controlEpoch = 4; f.values.controlMode = 'holding';
    await f.controller.progress(); // Epoch alone does not establish our request.
    expect(actionCalls(f)).toHaveLength(1);
    f.values.materialSnapshot.controlRequests.push({ operationId: request.params.operationId, action: 'requestControl',
      epoch: 4, owner: 'previous', mode: 'holding', paramsJson: JSON.stringify(request.params) });
    await f.controller.progress();
    expect(actionCalls(f)).toHaveLength(2);
    for (let n = 0; n < 4; n++) await f.controller.progress();
    expect(actionCalls(f)).toHaveLength(2);
    f.values.controlOwner = f.controller.owner; f.values.controlMode = 'manual';
    await f.controller.progress();
    expect(f.controller.flow?.phase).toBe('acquiring');
    const confirm = JSON.parse(String(actionCalls(f)[1][1]?.body));
    f.values.materialSnapshot.controlRequests.push({ operationId: confirm.params.operationId, action: 'confirmControl',
      epoch: 4, owner: f.controller.owner, mode: 'manual', paramsJson: JSON.stringify(confirm.params) });
    await f.controller.progress();
    expect(f.controller.flow?.phase).toBe('manual'); expect(actionCalls(f)).toHaveLength(2);
  });
  it('observes a lost confirmation response without repeating it or accepting owner alone', async () => {
    const f = fixture(); f.values.controlOwner = 'previous';
    await f.controller.begin('p', catalog, 'manual');
    const body = JSON.parse(String(actionCalls(f)[0][1]?.body));
    f.values.controlEpoch = 4; f.values.controlMode = 'holding';
    f.values.materialSnapshot.controlRequests.push({ operationId: body.params.operationId, action: 'requestControl',
      epoch: 4, owner: 'previous', mode: 'holding', paramsJson: JSON.stringify(body.params) });
    f.lose(); await f.controller.progress();
    for (let n = 0; n < 3; n++) { f.safeMeta.sampleTimeMs++; await f.controller.progress(); }
    f.values.controlOwner = f.controller.owner; f.values.controlMode = 'manual';
    for (let n = 0; n < 3; n++) await f.controller.progress();
    expect(actionCalls(f)).toHaveLength(2); expect(f.controller.flow?.phase).toBe('acquiring');
    const confirm = JSON.parse(String(actionCalls(f)[1][1]?.body));
    f.values.materialSnapshot.controlRequests.push({ operationId: confirm.params.operationId, action: 'confirmControl',
      epoch: 4, owner: f.controller.owner, mode: 'manual', paramsJson: JSON.stringify(confirm.params) });
    await f.controller.progress(); expect(f.controller.flow?.phase).toBe('manual');
  });
  it('binds configuration expectations during a mid-run first read without relabelling it as a fresh observed initial state', async () => {
    const f = fixture(), c = structuredClone(catalog);
    c.entities['/w'].config.tips = [{ id: 'T1', site: '/T1', bodyKey: '/tip' }];
    const material = f.values.materialSnapshot;
    material.vessels[0].volumeUl = 460; material.vessels[0].components[0].volumeUl = 460;
    material.tip.id = 'T1'; material.tip.volumeUl = 40; material.tip.components.push({ name: 'medium', volumeUl: 40 } as never);
    material.tips.push({ id: 'T1', state: 'installed' } as never);
    await f.controller.review('p', c);
    expect(f.controller.initialSummary).toMatchObject({ basis: 'configuration-expectation', volumeUl: 500, fresh: 'not-observed' });
    expect(material.vessels[0].volumeUl).toBe(460); expect(actionCalls(f)).toHaveLength(0);
    material.vessels[0].volumeUl = 450; material.vessels[0].components[0].volumeUl = 450;
    await expect(f.controller.review('p', c)).rejects.toThrow('与配置预期守恒不符');
    expect(f.controller.initialSummary?.volumeUl).toBe(500);
  });
  it('does not replace an existing expected baseline using a stale or silently changed Catalog', async () => {
    const f = fixture(); await f.controller.review('p', catalog);
    const c = structuredClone(catalog); c.sceneRevision = 'old';
    await expect(f.controller.review('p', c)).rejects.toThrow('Catalog');
    c.sceneRevision = 'r'; (c.entities['/w'].config.vessels as { volumeUl: number }[])[0].volumeUl = 450;
    await expect(f.controller.review('p', c)).rejects.toThrow('固定配方配置发生变化');
    expect(f.controller.initialSummary?.volumeUl).toBe(500); expect(actionCalls(f)).toHaveLength(0);
  });
  it('finds a protected/archived resolved receipt through the known invocation without recalculating from stock', async () => {
    const f = fixture(); f.plan.steps[0].status = 'failed';
    if ('do' in f.plan.steps[0]) f.plan.steps[0].do.action = 'aspirateFraction';
    f.plan.stepResults['200001'] = { invocationId: 'original' };
    const original = f.http.getMockImplementation()!;
    f.http.mockImplementation(async (path, options) => path.endsWith('/invocations/original') ? { result: { receipt: {
      operationId: 'liquid-1', status: 'failed', resolvedUl: 250, appliedUl: 40, remainingUl: 210,
    } } } : original(path, options));
    expect(await f.controller.receiptFor('p', f.plan.steps[0])).toMatchObject({ resolvedUl: 250, appliedUl: 40, remainingUl: 210 });
    expect(actionCalls(f)).toHaveLength(0);
  });
  it('reads reliable terminal events after live-window eviction and leaves missing receipts unknown', async () => {
    const f = fixture(); const original = f.http.getMockImplementation()!;
    f.http.mockImplementation(async (path, options) => path.includes('/events?') ? { events: [{ message: {
      type: 'entity.event', entityKey: '/w', event: 'operationCompleted', data: { receipt: { operationId: 'liquid-1', status: 'cancelled', appliedUl: 40 } },
    } }] } : original(path, options));
    expect(await f.controller.receiptFor('p', f.plan.steps[0])).toMatchObject({ appliedUl: 40 });
    if ('do' in f.plan.steps[0]) f.plan.steps[0].do.params.operationId = 'unknown';
    expect(await f.controller.receiptFor('p', f.plan.steps[0])).toBeUndefined();
    expect(actionCalls(f)).toHaveLength(0);
  });
  it('reviews noisy modeled environmental values but verifies readiness rather than exact temperature', async () => {
    const f = fixture(); const c = structuredClone(catalog);
    c.entities['/w'].config.managedDevices = ['/inc'];
    c.entities['/inc'] = { type: 'passage-incubator/v1' } as never;
    c.types['passage-incubator/v1'] = { properties: { controlSafe: {}, doorKnown: {}, temperatureC: {}, environmentReady: {} } } as never;
    let temperature = 37;
    const original = f.http.getMockImplementation()!;
    f.http.mockImplementation(async (path, options) => {
      const result = await original(path, options);
      if (path.includes('/state?')) (result as { entities: Record<string, unknown> }).entities['/inc'] = { values: { controlSafe: true, doorKnown: true, temperatureC: temperature, environmentReady: true }, meta: {} };
      return result;
    });
    const review = await f.controller.review('p', c); temperature = 37.02;
    await f.controller.prepareRemaining('p', review);
    const checked = f.http.mock.calls.filter(([path]) => path.endsWith('/check')).map(([, options]) => JSON.parse(String(options?.body)).property);
    expect(checked).toContain('environmentReady'); expect(checked).not.toContain('temperatureC');
  });
  it('reads a static physical reservoir without inventing an information-plane station', async () => {
    const f = fixture(); const c = structuredClone(catalog);
    c.entities['/w'].config.vessels = [{ id: 'source', entityKey: '/reservoir', site: '/reservoir/Content', capacityUl: 1000, volumeUl: 500, reagent: 'medium' }];
    f.values.materialSnapshot.vessels[0].entityKey = '/reservoir';
    const original = f.http.getMockImplementation()!;
    f.http.mockImplementation(async (path, options) => path.endsWith('/scene') ? { nodes: [{ entityKey: '/reservoir', kind: 'body' }], sites: [{ entityKey: '/reservoir/Content', parentBodyKey: '/reservoir' }] } :
      path.includes('/pose?') ? { p: [0, 0, 0], q: [0, 0, 0, 1], epoch: 0, sceneRevision: 'r' } : original(path, options));
    const review = await f.controller.review('p', c);
    expect(review.staticPoses).toHaveLength(1); expect(review.facts.some(fact => fact.entity === '/reservoir' && fact.property === 'station')).toBe(false);
    await f.controller.prepareRemaining('p', review);
  });
  it('requires a reviewed checkpoint before enabling any material dispatch', async () => {
    const f = fixture();
    await expect(f.controller.arm('p')).rejects.toThrow('读取现场核对');
    expect(actionCalls(f)).toHaveLength(0);
  });
  it('allows an explicitly reviewed manual owner to execute one controlled step', async () => {
    const f = fixture(); f.values.controlMode = 'manual';
    const review = await f.controller.review('p', catalog);
    await f.controller.prepareRemaining('p', review);
    await f.controller.arm('p', 'manual');
    expect(await f.controller.advance('p')).toContain('已受理');
    f.controller.stop();
    expect(f.controller.dispatching).toBe(false); expect(f.controller.observing).toBe(true);
    expect(await f.controller.advance('p')).toContain('running'); expect(actionCalls(f)).toHaveLength(1);
  });
  it('revises only unfinished controls and keeps completed content byte-for-byte', async () => {
    const f = fixture();
    const done = structuredClone(f.plan.steps[0]); done.id = 100001; done.status = 'done';
    if ('do' in done) done.do.params.owner = 'previous-owner';
    f.plan.steps.unshift(done);
    const review = await f.controller.review('p', catalog);
    await f.controller.prepareRemaining('p', review);
    const [, options] = f.http.mock.calls.find(([path]) => path.endsWith('/revisions'))!;
    const submitted = JSON.parse(String(options?.body));
    expect(submitted.steps[0]).toEqual({ ...done, status: 'pending' });
    expect(submitted.steps[1].do.params).toMatchObject({ owner: f.controller.owner, epoch: 3, volumeUl: 200 });
    expect(submitted.steps[1].do.params.operationId).toBe('liquid-1');
  });
  it('refuses failed transfer recovery without an explicit reviewed replacement', async () => {
    const f = fixture(); f.plan.steps[0].status = 'failed';
    const review = await f.controller.review('p', catalog);
    await expect(f.controller.prepareRemaining('p', review)).rejects.toThrow('明确修订');
    expect(f.http.mock.calls.some(([path]) => path.endsWith('/revisions'))).toBe(false);
  });
  it('rejects changed material after human inspection before revision', async () => {
    const f = fixture(); const review = await f.controller.review('p', catalog); f.values.materialRevision++; f.values.materialSnapshot.revision++;
    await expect(f.controller.prepareRemaining('p', review)).rejects.toThrow('先核对');
    expect(f.http.mock.calls.some(([path]) => path.endsWith('/revisions'))).toBe(false);
  });
  it('dispatches once with stable Plan association, then observes rather than repeating material transfer', async () => {
    const f = fixture(); await arm(f);
    expect(await f.controller.advance('p')).toContain('已受理');
    expect(await f.controller.advance('p')).toContain('running');
    expect(actionCalls(f)).toHaveLength(1);
    const body = JSON.parse(String(actionCalls(f)[0][1]?.body));
    expect(body).toMatchObject({ planId: 'p', stepId: 200001, planRevision: 1, params: { operationId: 'liquid-1', volumeUl: 200 } });
  });
  it('observes a normal source settling window without dispatching or inventing safety', async () => {
    const f = fixture(); await arm(f); f.values.controlSafe = false;
    expect(await f.controller.advance('p')).toContain('正在停稳');
    expect(f.controller.dispatching).toBe(true); expect(actionCalls(f)).toHaveLength(0);
    f.session.state = 'PAUSED'; f.session.simTime = 1;
    expect(await f.controller.advance('p')).toContain('冻结');
    f.session.state = 'RUNNING'; f.values.controlSafe = true;
    expect(await f.controller.advance('p')).toContain('已受理'); expect(actionCalls(f)).toHaveLength(1);
  });
  it('does not dispatch while simulation is paused', async () => {
    const f = fixture(); await arm(f); f.session.state = 'PAUSED';
    expect(await f.controller.advance('p')).toContain('冻结'); expect(actionCalls(f)).toHaveLength(0);
  });
  it('fences changed ownership before a later dispatch', async () => {
    const f = fixture(); await arm(f); f.values.controlEpoch++;
    expect(await f.controller.advance('p')).toContain('必须重新核对'); expect(actionCalls(f)).toHaveLength(0);
  });
  it('recovers a lost response from Plan invocation binding without a second POST', async () => {
    const f = fixture(); await arm(f); f.lose();
    expect(await f.controller.advance('p')).toContain('响应丢失');
    f.plan.stepResults['200001'] = { invocationId: 'i' };
    expect(await f.controller.advance('p')).toContain('running'); expect(actionCalls(f)).toHaveLength(1);
  });
  it('stops uncertain admission with no binding and never retries', async () => {
    const f = fixture(); await arm(f); f.lose(); await f.controller.advance('p');
    expect(await f.controller.advance('p')).toContain('禁止重发');
    await expect(f.controller.arm('p')).rejects.toThrow('在途调用'); expect(actionCalls(f)).toHaveLength(1);
  });
  it('requires authoritative done, not invocation success alone', async () => {
    const f = fixture(); await arm(f); await f.controller.advance('p'); f.setInvocation('succeeded');
    f.plan.steps[0].status = 'failed'; expect(await f.controller.advance('p')).toContain('计划期望未通过');
    expect(actionCalls(f)).toHaveLength(1);
  });
  it('fails closed for unreconciled materials', async () => {
    const f = fixture(); f.values.materialSnapshot.reconciled = false;
    await expect(f.controller.arm('p')).rejects.toThrow('物料'); expect(actionCalls(f)).toHaveLength(0);
  });
  it('refuses confirmation until accepted and running controlled calls reach terminal state', async () => {
    const f = fixture(); f.values.controlMode = 'holding';
    f.values.materialSnapshot.controlRequests.push({ action: 'requestControl', paramsJson: JSON.stringify({ nextOwner: f.controller.owner }) });
    // Preserve normal reads, substituting only the pending-invocation query.
    const original = f.http.getMockImplementation()!;
    f.http.mockImplementation(async (path, options) => path.includes('/invocations?status=accepted') ?
      { invocations: [{ invocationId: 'old', action: 'aspirate', params: { operationId: 'old-op' } }] } : original(path, options));
    await expect(f.controller.confirmControl('p')).rejects.toThrow('仍有已受理');
    expect(actionCalls(f)).toHaveLength(0);
  });
  it('requests handoff while paused without declaring physical safety or replaying work', async () => {
    const f = fixture(); f.session.state = 'PAUSED';
    await f.controller.requestControl('p', 'manual');
    const body = JSON.parse(String(actionCalls(f)[0][1]?.body));
    expect(body).toMatchObject({ action: 'requestControl', params: { epoch: 3, nextOwner: f.controller.owner, mode: 'manual' } });
    f.values.controlMode = 'holding'; f.values.controlSafe = false;
    await expect(f.controller.confirmControl('p')).rejects.toThrow('尚未实测停稳');
    expect(actionCalls(f)).toHaveLength(1);
  });
});
