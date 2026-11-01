import { describe, expect, it, vi } from 'vitest';
import type { EntityValues, Invocation } from '../protocol';
import type { PlanDocument } from '../plans/model';
import { collectCycleEvidence } from './cycles';
import { object } from './model';

function fixture() {
  const entity = '/World/Lab/Centrifuge';
  const target = { sessionId: 's', sceneRevision: 'r', epoch: 11 }; // Session epoch is not control epoch 7.
  const bodies = Object.fromEntries(['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'B5', 'B6'].map(name => [name, `/body/${name}`]));
  const invocations: Record<string, Invocation> = {};
  const plans = [3, 4].map(stage => {
    const params = { rpm: 500, durationSeconds: 2, owner: 'old-auto-owner', epoch: 7, operationId: `batch-${stage}` };
    const id = `inv-${stage}`, stepId = stage * 100000 + 1;
    const load = (stage === 3 ? ['S1', 'S2', 'S3', 'S4'] : ['S5', 'S6', 'B5', 'B6']).map(name => bodies[name]);
    invocations[id] = { invocationId: id, requestId: `request-${stage}`, entityKey: entity, action: 'start', status: 'succeeded', params, epoch: target.epoch,
      actor: { kind: 'cli', name: 'old-runner' }, createdSimTime: 20, createdAtMs: 1, startedSimTime: 20 + stage - 2.5, endedSimTime: 20 + stage,
      result: { schema: 'lw.centrifuge-cycle-receipt/1', entityKey: entity, invocationId: id, operationId: params.operationId,
        owner: params.owner, epoch: params.epoch, completedCycle: params.operationId, completedLoad: load,
        cycleRunSeconds: 2 - Number.EPSILON, actualRotorTurns: 1.5, actualRpm: 0, rotorStopped: true, lidClosed: true, completedSimTime: 20 + stage } } as Invocation;
    return { ...target, id: `p-${stage}`, target: 'session', steps: [{ id: stepId, status: 'done', do: { entity, action: 'start', params: { ...params } } }],
      stepResults: { [stepId]: { invocationId: id } } } as unknown as PlanDocument;
  });
  const read = vi.fn(async (path: string) => {
    const id = path.split('/').at(-1)!; if (!invocations[id]) throw new Error('missing archive'); return structuredClone(invocations[id]);
  });
  return { entity, target, bodies, plans, invocations, read };
}
const collect = (f: ReturnType<typeof fixture>, snapshots = new Map<number, Record<string, EntityValues>>()) =>
  collectCycleEvidence(f.read as never, '/sessions/s', f.plans, f.bodies, f.target, snapshots);

describe('public Plan-bound retained centrifuge result consumer', () => {
  it('accepts measured old-owner receipts after refresh without stage snapshots, retaining the clock representation', async () => {
    const f = fixture(), report = await collect(f);
    expect(report.status).toBe('cycle_evidence_valid');
    expect(report.batches.every(batch => !batch.stageSnapshotAvailable)).toBe(true);
    expect(report.batches[0].receipt).toMatchObject({ owner: 'old-auto-owner', epoch: 7, cycleRunSeconds: 2 - Number.EPSILON });
    expect(f.read.mock.calls.map(([path]) => path)).toEqual(['/sessions/s/invocations/inv-3', '/sessions/s/invocations/inv-4']);
  });
  it.each([
    ['owner', 'new-human-owner'], ['actualRotorTurns', 0], ['actualRotorTurns', null], ['actualRotorTurns', NaN],
    ['actualRpm', Infinity], ['actualRpm', 1], ['cycleRunSeconds', 1.99], ['completedSimTime', 100], ['rotorStopped', false], ['lidClosed', false],
  ])('rejects incompatible or unknown %s evidence without changing gates', async (field, value) => {
    const f = fixture(); object(f.invocations['inv-3'].result)![field] = value;
    expect((await collect(f)).status).toBe('invalid');
  });
  it('enforces the actual declared Plan duration as well as the fixed two-second minimum', async () => {
    const f = fixture(); if ('do' in f.plans[0].steps[0]) f.plans[0].steps[0].do.params.durationSeconds = 3;
    f.invocations['inv-3'].params.durationSeconds = 3;
    expect((await collect(f)).status).toBe('invalid');
  });
  it('rejects a missing numeric measurement rather than treating it as zero or a completed Plan', async () => {
    const f = fixture(); delete object(f.invocations['inv-3'].result)!.cycleRunSeconds;
    expect((await collect(f)).status).toBe('invalid');
  });
  it('rejects wrong loads and requires any locally captured stage state to agree exactly', async () => {
    const f = fixture(); const receipt = object(f.invocations['inv-3'].result)!;
    const snapshot = { key: f.entity, values: { completedCycle: receipt.completedCycle, completedLoad: receipt.completedLoad,
      cycleRunSeconds: receipt.cycleRunSeconds, actualRotorTurns: receipt.actualRotorTurns }, meta: {}, rev: 1 } as EntityValues;
    const snapshots = new Map([[3, { [f.entity]: snapshot }]]);
    expect((await collect(f, snapshots)).status).toBe('cycle_evidence_valid');
    snapshot.values.completedLoad = ['/body/S5']; expect((await collect(f, snapshots)).status).toBe('invalid');
    receipt.completedLoad = ['/body/S5', '/body/S6', '/body/B5', '/body/B6'];
    expect((await collect(f)).status).toBe('invalid');
  });
  it('keeps legacy None, missing binding and disabled/missing archive unavailable instead of inventing completion', async () => {
    const f = fixture(); f.invocations['inv-3'].result = null;
    expect((await collect(f)).status).toBe('unavailable');
    f.read.mockRejectedValue(new Error('503 recording history unavailable'));
    expect((await collect(f)).status).toBe('unavailable');
    f.plans[0].stepResults = {}; expect((await collect(f)).status).toBe('unavailable');
  });
  it('does not read another session epoch or accept an invocation different from the current Plan params', async () => {
    const f = fixture(); f.invocations['inv-3'].epoch = 12;
    expect((await collect(f)).status).toBe('unavailable');
    f.invocations['inv-3'].epoch = 11; f.invocations['inv-3'].params.owner = 'different';
    expect((await collect(f)).status).toBe('invalid');
    f.target.sceneRevision = 'new'; expect((await collect(f)).status).toBe('unavailable');
  });
});
