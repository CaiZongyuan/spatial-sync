import { describe, expect, it, vi } from 'vitest';
import { PassageController } from './controller';
import type { Catalog } from '../protocol';
import type { PlanDocument } from '../plans/model';

function fixture() {
  const session = { sessionId: 's', sceneRevision: 'r', epoch: 0, state: 'RUNNING', simTime: 1 };
  const values = { controlOwner: '', controlEpoch: 3, controlMode: 'auto', controlSafe: true, materialRevision: 4,
    auditPassed: true, materialSnapshot: { revision: 4, reconciled: true, tips: [], tip: { id: '', volumeUl: 0 }, vessels: [], reservations: [], observations: [], operations: [], controlRequests: [] as unknown[] } };
  const plan = { id: 'p', kind: 'operation', target: 'session', sessionId: 's', sceneRevision: 'r', epoch: 0, revision: 1,
    status: 'approved', steps: [{ id: 200001, status: 'pending', do: { entity: '/w', action: 'aspirate', params: { owner: '', epoch: 3, operationId: 'liquid-1', vessel: 'mother', volumeUl: 200 } } }], stepResults: {} } as unknown as PlanDocument;
  let invocation = { invocationId: 'i', status: 'running' };
  let loseResponse = false;
  const http = vi.fn(async (path: string, options?: RequestInit): Promise<unknown> => {
    if (path === '/sessions/s') return structuredClone(session);
    if (path === '/plans/p') return structuredClone(plan);
    if (path.includes('/state?')) return { entities: { '/w': { key: '/w', rev: 1, values: structuredClone(values), meta: {} } } };
    if (path.includes('/invocations?')) return { invocations: [] };
    if (path.endsWith('/check')) return { satisfied: true };
    if (path.endsWith('/revisions')) return structuredClone(plan);
    if (path === '/sessions/s/actions') {
      if (loseResponse) throw new TypeError('network response lost');
      return structuredClone(invocation);
    }
    if (path.includes('/invocations/')) return structuredClone(invocation);
    throw new Error(`Unexpected request ${path}`);
  });
  const controller = new PassageController('s', '/w', http as never);
  values.controlOwner = controller.owner;
  if ('do' in plan.steps[0]) plan.steps[0].do.params.owner = controller.owner;
  return { controller, session, values, plan, http, setInvocation: (status: string) => { invocation = { ...invocation, status }; }, lose: () => { loseResponse = true; } };
}
const catalog = { entities: { '/w': { type: 'cell-passage-workcell/v1', config: { managedDevices: ['/w'], vessels: [{ entityKey: '/w' }] } } }, types: { 'cell-passage-workcell/v1': { properties: { controlSafe: {} } } } } as unknown as Catalog;
async function arm(f: ReturnType<typeof fixture>) { const review = await f.controller.review('p', catalog); await f.controller.prepareRemaining('p', review); await f.controller.arm('p'); }
const actionCalls = (f: ReturnType<typeof fixture>) => f.http.mock.calls.filter(([path]) => path.endsWith('/actions'));
describe('passage browser scheduling through existing HTTP/Plan API', () => {
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
    expect(submitted.steps[1].do.params.operationId).not.toBe('liquid-1');
  });
  it('refuses failed transfer recovery without an explicit reviewed replacement', async () => {
    const f = fixture(); f.plan.steps[0].status = 'failed';
    const review = await f.controller.review('p', catalog);
    await expect(f.controller.prepareRemaining('p', review)).rejects.toThrow('明确修订');
    expect(f.http.mock.calls.some(([path]) => path.endsWith('/revisions'))).toBe(false);
  });
  it('rejects changed material after human inspection before revision', async () => {
    const f = fixture(); const review = await f.controller.review('p', catalog); f.values.materialRevision++;
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
