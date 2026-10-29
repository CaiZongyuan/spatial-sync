import { describe, expect, it } from 'vitest';
import type { Catalog, EntityValues, JsonValue } from '../protocol';
import type { PlanDocument } from '../plans/model';
import { PassageController } from './controller';
import { object } from './model';

function roundFixture() {
  const plans = [3, 6, 4, 3, 5, 3, 2].flatMap((parts, index) => Array.from({ length: parts }, (_, part) => ({ id: `cp-r-stage${index + 1}-part${part + 1}-of${parts}`,
    title: `Cell Passage r · ${index + 1}/7 · stage · part ${part + 1}/${parts}`, target: 'session', kind: 'operation',
    sessionId: 's', sceneRevision: 'r', epoch: 0, revision: 1, status: 'approved', stepResults: {},
    steps: [{ id: (index + 1) * 100000 + part + 1, status: 'pending', do: { entity: '/w', action: 'wait', params: { owner: 'old', epoch: 0, operationId: `global-${index}-${part}`, seconds: 1 } } }],
  }))) as unknown as PlanDocument[];
  const entity: EntityValues = { key: '/w', rev: 1, meta: {}, values: { controlOwner: '', controlEpoch: 4, controlMode: 'auto',
    controlSafe: true, materialRevision: 0, auditPassed: true, materialSnapshot: { operationCount: 0, revision: 0, reconciled: true,
      vessels: [], tips: [], tip: { id: '', volumeUl: 0, components: [], cells: [] }, reservations: [], observations: [], operations: [], controlRequests: [] } } };
  const admitted: { planId: string; params: Record<string, JsonValue> }[] = [];
  const controls: string[] = [];
  const session = { sessionId: 's', sceneRevision: 'r', epoch: 0, simTime: 1, state: 'RUNNING' };
  const behavior = { holdSafe: true, dropAdmission: false };
  let next = { owner: '', mode: 'auto' };
  const catalog = { sceneRevision: 'r', entities: { '/w': { type: 'cell-passage-workcell/v1', config: { managedDevices: [], vessels: [], tips: [] } } },
    types: { 'cell-passage-workcell/v1': { properties: { controlSafe: {} } } } } as unknown as Catalog;
  const http = async (path: string, options?: RequestInit): Promise<unknown> => {
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    if (path === '/sessions/s') return structuredClone(session);
    if (path.includes('/state?')) return { entities: { '/w': structuredClone(entity) } };
    if (path.includes('/plans?')) return { plans: structuredClone(plans) };
    const plan = plans.find(plan => path === `/plans/${plan.id}` || path === `/plans/${plan.id}/revisions`);
    if (plan && options?.method === 'POST') {
      plan.steps = body.steps; plan.revision++; return structuredClone(plan);
    }
    if (plan) return structuredClone(plan);
    if (path.endsWith('/check')) return { satisfied: true };
    if (path.includes('/invocations?')) return { invocations: [] };
    if (path.endsWith('/commands')) { session.state = body.command === 'pause' ? 'PAUSED' : 'RUNNING'; return {}; }
    if (path.endsWith('/actions')) {
      if (body.action === 'requestControl') {
        controls.push(body.action); entity.values.controlEpoch = Number(entity.values.controlEpoch) + 1; entity.values.controlMode = 'holding'; entity.values.controlSafe = behavior.holdSafe;
        next = { owner: body.params.nextOwner, mode: body.params.mode };
        (object(entity.values.materialSnapshot)!.controlRequests as JsonValue[]).push({ action: 'requestControl', operationId: body.params.operationId,
          epoch: entity.values.controlEpoch, owner: entity.values.controlOwner, mode: entity.values.controlMode, paramsJson: JSON.stringify(body.params) });
        return { invocationId: 'request', status: 'accepted' };
      }
      if (body.action === 'confirmControl') {
        if (entity.values.controlSafe !== true) throw new Error('fixture rejects unsafe confirmation');
        controls.push(body.action); entity.values.controlOwner = next.owner; entity.values.controlMode = next.mode;
        (object(entity.values.materialSnapshot)!.controlRequests as JsonValue[]).push({ action: 'confirmControl', operationId: body.params.operationId,
          epoch: entity.values.controlEpoch, owner: entity.values.controlOwner, mode: entity.values.controlMode, paramsJson: JSON.stringify(body.params) });
        return { invocationId: 'confirm', status: 'accepted' };
      }
      const active = plans.find(plan => plan.id === body.planId)!;
      admitted.push({ planId: body.planId, params: body.params });
      active.steps[0].status = 'done'; active.status = 'completed';
      active.stepResults[String(active.steps[0].id)] = { invocationId: `i-${admitted.length}`, invocationIds: [`i-${admitted.length}`] };
      entity.values.materialRevision = Number(entity.values.materialRevision) + 1;
      object(entity.values.materialSnapshot)!.revision = entity.values.materialRevision;
      object(entity.values.materialSnapshot)!.operationCount = admitted.length;
      if (behavior.dropAdmission) { behavior.dropAdmission = false; throw new TypeError('admitted response lost'); }
      return { invocationId: `i-${admitted.length}`, status: 'succeeded' };
    }
    if (path.includes('/invocations/')) return { status: 'succeeded' };
    throw new Error(path);
  };
  const controller = new PassageController('s', '/w', http as never);
  entity.values.controlOwner = controller.owner;
  return { plans, entity, catalog, controller, admitted, session, behavior, controls };
}

describe('one browser owner across a complete round', () => {
  it('chains fresh control/standstill/revision without an unnecessary human review checkbox', async () => {
    const f = roundFixture(); f.entity.values.controlOwner = 'external'; const first = f.plans[0].id;
    await f.controller.begin(first, f.catalog, 'auto'); expect(f.admitted).toHaveLength(0);
    expect(f.entity.values.controlMode).toBe('holding'); expect(f.controls).toEqual(['requestControl']);
    await f.controller.progress(); expect(f.controls).toEqual(['requestControl', 'confirmControl']);
    await f.controller.progress(); expect(f.controller.flow?.phase).toBe('running');
    expect(f.plans.every(plan => plan.revision === 2)).toBe(true); expect(f.admitted).toHaveLength(0);
    await f.controller.progress(); expect(f.admitted).toHaveLength(1);
  });
  it('keeps paused simTime frozen and requires explicit physical resume before unsafe hold can be confirmed', async () => {
    const f = roundFixture(); f.entity.values.controlOwner = 'external'; f.session.state = 'PAUSED'; f.behavior.holdSafe = false;
    await f.controller.begin(f.plans[0].id, f.catalog, 'manual'); await f.controller.progress();
    expect(f.controller.flow?.phase).toBe('paused-hold'); expect(f.controls).toEqual(['requestControl']); expect(f.session.simTime).toBe(1);
    await f.controller.continueFlow(); expect(f.session.state).toBe('RUNNING'); expect(f.controller.dispatching).toBe(false);
    await f.controller.progress(); expect(f.controls).toEqual(['requestControl']); expect(f.admitted).toHaveLength(0);
    f.entity.values.controlSafe = true; await f.controller.progress(); await f.controller.progress();
    expect(f.controls).toEqual(['requestControl', 'confirmControl']); expect(f.controller.flow?.phase).toBe('manual'); expect(f.admitted).toHaveLength(0);
  });
  it('executes exactly one step through the simplified single-step flow then stops dispatch', async () => {
    const f = roundFixture(); await f.controller.begin(f.plans[0].id, f.catalog, 'auto', true);
    await f.controller.progress(); await f.controller.progress();
    expect(f.admitted).toHaveLength(1); expect(f.controller.flow?.phase).toBe('ready'); expect(f.controller.polling).toBe(false);
  });
  it('continues automatically when source confirms manual ownership before the client observes its manual phase', async () => {
    const f = roundFixture(); f.entity.values.controlOwner = 'external';
    await f.controller.begin(f.plans[0].id, f.catalog, 'manual'); await f.controller.progress();
    expect(f.entity.values.controlMode).toBe('manual'); expect(f.controller.flow?.phase).toBe('acquiring');
    await f.controller.continueFlow();
    expect(f.controller.flow?.mode).toBe('auto');
    await f.controller.progress(); await f.controller.progress();
    expect(f.controller.flow?.phase).toBe('running'); expect(f.admitted).toHaveLength(0);
  });
  it('pause fences the source even before a run flow was started', async () => {
    const f = roundFixture(); f.entity.values.controlOwner = 'external'; await f.controller.pause();
    expect(f.session.state).toBe('PAUSED'); expect(f.session.simTime).toBe(1); expect(f.entity.values.controlEpoch).toBe(5);
    expect(f.entity.values.controlMode).toBe('holding'); expect(f.admitted).toHaveLength(0);
  });
  it('observes a lost response through the bound invocation before automatic continuation, without replay', async () => {
    const f = roundFixture(); f.behavior.dropAdmission = true;
    await f.controller.begin(f.plans[0].id, f.catalog, 'auto');
    expect(await f.controller.progress()).toContain('响应丢失'); expect(f.controller.polling).toBe(true);
    expect(await f.controller.progress()).toContain('当前步骤已完成');
    for (let index = 0; index < 100 && f.controller.polling; index++) await f.controller.progress();
    expect(f.admitted).toHaveLength(26); expect(new Set(f.admitted.map(row => row.params.operationId)).size).toBe(26);
  });
  it('rebinds and serializes every stage part without resetting materials or operation identities', async () => {
    const f = roundFixture(); const first = f.plans[0].id;
    const identities = f.plans.map(plan => 'do' in plan.steps[0] ? plan.steps[0].do.params.operationId : undefined);
    await f.controller.prepareRemaining(first, await f.controller.review(first, f.catalog));
    expect(f.plans.every(plan => plan.revision === 2)).toBe(true);
    await f.controller.arm(first);
    for (let index = 0; index < 100 && f.controller.dispatching; index++) await f.controller.advance(first);
    expect(f.admitted.map(row => row.planId)).toEqual(f.plans.map(plan => plan.id));
    expect(f.admitted).toHaveLength(26);
    expect(f.admitted.map(row => row.params.operationId)).toEqual(identities);
    expect(f.admitted.every(row => row.params.owner === f.controller.owner && row.params.epoch === 4)).toBe(true);
    expect(f.entity.values.materialRevision).toBe(26);
  });
  it('refuses to prepare a partially published round', async () => {
    const f = roundFixture(); const first = f.plans[0].id; f.plans.pop();
    await expect(f.controller.prepareRemaining(first, await f.controller.review(first, f.catalog))).rejects.toThrow('缺失');
    expect(f.admitted).toHaveLength(0); expect(f.plans.every(plan => plan.revision === 1)).toBe(true);
  });
  it('stops at the part boundary if another client changes a later revision', async () => {
    const f = roundFixture(); const first = f.plans[0].id;
    await f.controller.prepareRemaining(first, await f.controller.review(first, f.catalog)); await f.controller.arm(first);
    await f.controller.advance(first); await f.controller.advance(first); f.plans[1].revision++;
    expect(await f.controller.advance(first)).toContain('分段已改变'); expect(f.admitted).toHaveLength(1);
  });
});
