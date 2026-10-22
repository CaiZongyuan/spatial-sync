import { expect, test, vi } from 'vitest';
import { FramingState } from '../scene/camera-framing';
import type { SessionClient } from '../stream/client';
import type { PlanDocument } from './model';

const harness = vi.hoisted(() => ({ response: {} as Record<string, unknown> }));
vi.mock('react', () => ({
  useSyncExternalStore: (_subscribe: unknown, snapshot: () => unknown) => snapshot(),
  useEffect: () => {},
  useState: (initial: unknown) => [typeof initial === 'number' ? 0 : harness.response, vi.fn()],
  useCallback: (value: unknown) => value,
}));
import { useMotionPreview } from './hooks';

test('a first pose before parent context refresh cannot consume the requested preview fit', () => {
  const buffer = { epoch: -1, latestSample: undefined as unknown };
  const view = { connection: 'connected', sessionState: 'RUNNING', manifest: { sceneRevision: 'rev-preview' } };
  const client = { sessionId: 'preview-session', readOnly: false, buffer,
    subscribe: vi.fn(), getSnapshot: () => view } as unknown as SessionClient;
  const step: PlanDocument['steps'][number] = { id: 1, status: 'pending',
    do: { entity: '/Robot', action: 'moveJoints', params: {} } };
  const plan: PlanDocument = { id: 'plan-preview', kind: 'operation', title: 'Framing prerequisite fixture',
    status: 'approved', revision: 1, target: 'session', steps: [step],
    sessionId: client.sessionId, sceneRevision: view.manifest.sceneRevision, epoch: 0,
    requestedBy: { kind: 'agent', name: 'framing-test' }, createdAtMs: 1,
    approvalId: null, approvalStatus: null, approvalMode: 'auto', stepResults: {}, changes: [], persistence: { status: 'stored' } };
  const scope = { planId: plan.id, revision: 1, stepId: 1, sessionId: client.sessionId,
    sceneRevision: plan.sceneRevision, epoch: 0 };
  const read = () => useMotionPreview(client, plan, plan, plan.steps[0]);
  const pending = (preview: ReturnType<typeof read>) => Boolean(preview.framingPending);
  const framing = new FramingState();
  harness.response = {};
  const beforeParentRefresh = read();
  buffer.epoch = 0;
  buffer.latestSample = { seq: 0, epoch: 0 }; // Pose subscription can render before the parent's ViewState changes.
  expect(framing.takeFit(() => !pending(beforeParentRefresh))).toBe(false);
  const requested = read();
  expect(framing.takeFit(() => !pending(requested))).toBe(false);
  harness.response = { identity: `true:${JSON.stringify(scope)}`,
    value: { ...scope, available: false, reason: 'Disclosed fixture: action has no path' } };
  expect(framing.takeFit(() => !pending(read()))).toBe(true);
  expect(framing.takeFit(() => !pending(read()))).toBe(false);
});
