import { describe, expect, it } from 'vitest';
import type { EntityEvent, OperationPlan, PlanFullUpdate, PlanStepUpdate } from '../protocol';
import { PlanStore } from './store';
import { planChanges, previewBodies, previewMatches, stepCommand, stepInvocationStatus, type MotionPreview } from './model';
import { validateManifest } from '../validate-manifest';
import syntheticManifest from '../../../../../assets/scene-previews/lab-demo/2/d29d122e8068a592c58925f1d21c355496f9b4dc6761c92897e22538c9128f39/synthetic/manifest.json';

const plan: OperationPlan = { id: 'plan-1', kind: 'operation', title: 'Inspect motion', target: 'session',
  revision: 1, status: 'approved', steps: [
    { id: 1, status: 'pending', do: { entity: '/arm', action: 'moveJoints', params: { positions: [1, .2] } } },
    { id: 2, status: 'pending', check: { entity: '/device', property: 'lidOpen', equals: false } },
  ] };
const full = (overrides: Partial<PlanFullUpdate> = {}): PlanFullUpdate => ({ type: 'plan.update', update: 'full',
  sceneRevision: 'scene', epoch: 1, simTime: 0, sampleTimeMs: 100, plan: structuredClone(plan), ...overrides });
const step = (overrides: Partial<PlanStepUpdate> = {}): PlanStepUpdate => ({ type: 'plan.update', update: 'step',
  sceneRevision: 'scene', epoch: 1, simTime: 1, sampleTimeMs: 200, planId: plan.id, revision: 1,
  stepId: 1, status: 'done', ...overrides });
const association = (data: EntityEvent['data'] = {}): EntityEvent => ({ type: 'entity.event', sceneRevision: 'scene',
  epoch: 1, simTime: .5, sampleTimeMs: 150, entityKey: '/arm', event: 'plan.invocation',
  data: { planId: plan.id, revision: 1, stepId: 1, invocationId: 'call-1', outOfOrder: false, outOfPlan: false, ...data } });

describe('shared plan FIFO state', () => {
  it('applies actual full/step messages immutably and rejects mismatched scope/revision/step/time', () => {
    const store = new PlanStore(); store.configure('scene', 1);
    const value = full(); store.apply(value);
    value.plan.title = 'Mutated caller';
    const before = store.getSnapshot();
    store.apply(step());
    expect(before.entries[0].plan.steps[0].status).toBe('pending');
    expect(store.getSnapshot().entries[0].plan.steps[0].status).toBe('done');
    for (const stale of [step({ revision: 2 }), step({ epoch: 0 }), step({ sceneRevision: 'other' }),
      step({ stepId: 99 }), step({ status: 'pending', sampleTimeMs: 150 }), full({ sampleTimeMs: 50 })]) store.apply(stale);
    expect(store.getSnapshot().entries[0].plan).toMatchObject({ title: 'Inspect motion',
      steps: [{ status: 'done' }, { status: 'pending' }] });
    store.apply(full({ plan: { ...plan, revision: 2, steps: [{ ...plan.steps[0], status: 'done' },
      { ...plan.steps[1], check: { entity: '/device', property: 'lidOpen', equals: true } }] }, sampleTimeMs: 300 }));
    store.apply(step({ revision: 1, status: 'failed', sampleTimeMs: 400 }));
    expect(store.getSnapshot().entries[0]).toMatchObject({ plan: { revision: 2, steps: [{ status: 'done' }, {}] },
      changes: [{ stepId: 2, change: 'changed' }] });
  });

  it('clears prior epoch and same-scope seek state; does not fabricate associations from invocations', () => {
    const store = new PlanStore(); store.configure('scene', 1); store.apply(full());
    store.apply(association());
    store.apply(association({ invocationId: 'outside', planId: null, revision: null, stepId: null, outOfPlan: true }));
    expect(store.getSnapshot().entries[0].associations[1].invocationIds).toEqual(['call-1']);
    expect(store.getSnapshot().deviations[0]).toMatchObject({ invocationId: 'outside', outOfPlan: true });
    store.advanceEpoch(2);
    store.apply(full({ sampleTimeMs: 400 }));
    expect(store.getSnapshot()).toEqual({ entries: [], deviations: [] });
    store.apply(full({ epoch: 2 }));
    expect(store.getSnapshot().entries[0].associations).toEqual({});
    store.configure('scene', 2);
    expect(store.getSnapshot().entries).toEqual([]);
    store.apply(full({ epoch: 2, plan: { ...plan, revision: 3 } }));
    expect(store.getSnapshot().entries[0].changes).toBeUndefined();
  });

  it('keeps explicit completed-step association through a revision and ignores old association revisions', () => {
    const store = new PlanStore(); store.configure('scene', 1); store.apply(full());
    store.apply(association()); store.apply(step());
    store.apply(full({ sampleTimeMs: 300, plan: { ...plan, revision: 2,
      steps: [{ ...plan.steps[0], status: 'done' }, plan.steps[1]] } }));
    store.apply(association({ revision: 1, invocationId: 'late', outOfOrder: true }));
    expect(store.getSnapshot().entries[0].associations[1].invocationIds).toEqual(['call-1']);
    expect(store.getSnapshot().deviations).toEqual([]);
  });

  it('does not label a difference across missing revisions as the immediately previous revision', () => {
    const store = new PlanStore(); store.configure('scene', 1); store.apply(full());
    const third = { ...plan, revision: 3, title: 'Two unseen revisions' };
    store.apply(full({ sampleTimeMs: 300, plan: third }));
    expect(store.getSnapshot().entries[0].changes).toBeUndefined();
    store.apply(full({ sampleTimeMs: 400, plan: third }));
    expect(store.getSnapshot().entries[0].changes).toBeUndefined();
    store.apply(full({ sampleTimeMs: 500, plan: { ...third, revision: 4, title: 'Adjacent revision' } }));
    expect(store.getSnapshot().entries[0].changes).toEqual([{ change: 'title' }]);
  });
});

it('describes actual revision content and order without confusing status or JSON key order', () => {
  const first: OperationPlan = { ...plan, steps: [{ id: 1, status: 'done', do: { entity: '/arm', action: 'moveTo',
    params: { location: 'rack.A1', speed: .5 } } }, plan.steps[1]] };
  const after: OperationPlan = { ...first, title: 'New title', revision: 2, steps: [plan.steps[1],
    { ...first.steps[0], status: 'pending', do: { entity: '/arm', action: 'moveTo', params: { speed: .5, location: 'rack.A1' } } },
    { id: 3, status: 'pending', wait: { entity: '/device', property: 'phase', equals: 'idle' } }] };
  expect(planChanges(first, after)).toEqual([{ change: 'title' }, { stepId: 2, change: 'moved' },
    { stepId: 1, change: 'moved' }, { stepId: 3, change: 'added' }]);
  expect(planChanges(first, { ...first, steps: [] })).toEqual([{ stepId: 1, change: 'removed' }, { stepId: 2, change: 'removed' }]);
});

it('formats real correlated CLI action/wait/check arguments and shell-specific quotes', () => {
  expect(stepCommand(plan, plan.steps[0], 'bash')).toBe("lw do /arm moveJoints 'positions=[1,0.2]' --plan plan-1 --step 1 --plan-revision 1");
  expect(stepCommand(plan, plan.steps[1], 'powershell')).toBe('lw check /device lidOpen equals false --plan plan-1 --step 2 --plan-revision 1');
  expect(stepCommand(plan, { id: 3, status: 'pending', wait: { entity: '/device', property: 'temperatureC',
    deviatesFrom: 'setpointC', by: 2 }, timeoutSeconds: 15 }, 'bash')).toBe(`lw wait /device temperatureC deviatesFrom '"setpointC"' --by 2 --timeout 15 --plan plan-1 --step 3 --plan-revision 1`);
  const quoted = { id: 4, status: 'pending' as const, check: { entity: '/device', property: 'name', equals: "O'Brien lab" } };
  expect(stepCommand(plan, quoted, 'powershell')).toContain(`'"O''Brien lab"'`);
  expect(stepCommand(plan, quoted, 'powershell')).toContain(`'"""O''Brien lab"""'`);
  expect(stepCommand(plan, quoted, 'bash')).toContain(`'"O'\\''Brien lab"'`);
});

it('keeps JSON-looking condition strings distinct from scalar values in copied commands', () => {
  for (const [value, argument] of [['false', `'"false"'`], ['null', `'"null"'`], ['12', `'"12"'`],
    [false, 'false'], [null, 'null'], [12, '12']] as const) {
    const check = { id: 7, status: 'pending' as const, check: { entity: '/device', property: 'location', equals: value } };
    expect(stepCommand(plan, check, 'bash')).toBe(`lw check /device location equals ${argument} --plan plan-1 --step 7 --plan-revision 1`);
    expect(stepCommand(plan, check, 'powershell')).toContain(argument);
  }
});

it('does not assign the latest retry status to a missing historical attempt', () => {
  const result = { invocationId: 'retry-success', invocationIds: ['first-failed', 'retry-success'], invocationStatus: 'succeeded' };
  expect(stepInvocationStatus(result, 'first-failed')).toBeUndefined();
  expect(stepInvocationStatus(result, 'retry-success')).toBe('succeeded');
  expect(stepInvocationStatus(result, 'first-failed', { status: 'failed' })).toBe('failed');
  expect(stepInvocationStatus(result, 'retry-success', { status: 'running' })).toBe('running');
});

it('rejects stale or nonfinite preview geometry and accepts disclosed policy target-only DTO', () => {
  const scope = { planId: plan.id, revision: 1, stepId: 1, sessionId: 's', sceneRevision: 'scene', epoch: 1 };
  const preview: MotionPreview = { ...scope, available: true, entityKey: '/arm', simTime: 0, sampleTimeMs: 10,
    points: [[0, 0, 1], [1, 0, 1]], startJointPositions: [0], targetJointPositions: [1],
    targetPose: { p: [1, 0, 1], q: [0, 0, 0, 1] }, targetBodyPoses: {}, policyDriven: false };
  expect(previewMatches(preview, scope)).toBe(true);
  expect(previewMatches({ ...preview, epoch: 2 }, scope)).toBe(false);
  expect(previewMatches({ ...preview, points: [[Number.NaN, 0, 1]] }, scope)).toBe(false);
  expect(previewMatches({ ...preview, points: [], policyDriven: true }, scope)).toBe(true);
  expect(previewMatches({ ...preview, points: [] }, scope)).toBe(false);
  const actual = validateManifest(syntheticManifest);
  const keys = ['/World/Lab/Robot/Link1', '/World/Lab/Robot/Link2', '/World/Lab/RobotA/Hand'];
  const bodies = previewBodies(actual, { ...preview, entityKey: '/World/Lab/RobotA',
    targetBodyPoses: Object.fromEntries(keys.map(key => [key, preview.targetPose])) });
  expect(bodies.map(value => value.node.entityKey).sort()).toEqual(keys.sort());
  expect(bodies.filter(value => !value.node.entityKey.startsWith('/World/Lab/RobotA/'))).toHaveLength(2);
});
