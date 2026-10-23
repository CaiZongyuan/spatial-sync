import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test';
import type {} from '../../apps/web/src/components/Workbench';
import type {} from '../../apps/web/src/components/PlansPage';
import type { AvailablePreview, PlanDocument } from '../../apps/web/src/lib/plans/model';

export const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
export const centrifuge = '/World/Lab/Centrifuge';
export const robot = '/World/Lab/RobotA';
export const headers = { 'X-Actor': 'agent:issue26-e2e' };
export const condition = { entity: centrifuge, property: 'lidOpen', equals: false };
export const move = (id: number, positions = [1, .5, -.5, .1, .2, .3, .4]) =>
  ({ id, do: { entity: robot, action: 'moveJoints', params: { positions } } });
export const close = { id: 1, do: { entity: centrifuge, action: 'closeLid' }, expect: condition };

export async function json<T>(response: Awaited<ReturnType<APIRequestContext['get']>>): Promise<T> {
  expect(response.ok(), await response.text()).toBe(true);
  return response.json() as Promise<T>;
}
export async function createSession(request: APIRequestContext) {
  return json<{ sessionId: string }>(await request.post(`${api}/api/v1/sessions`, {
    data: { sceneVersion: 'lab-demo@2', source: 'synthetic', continueWithoutObservers: true },
  }));
}
export async function submit(request: APIRequestContext, title: string, steps: unknown[]) {
  return approve(request, await json<PlanDocument>(await request.post(`${api}/api/v1/plans`, {
    headers, data: { title, target: 'session', steps },
  })));
}
export async function approve(request: APIRequestContext, plan: PlanDocument) {
  // The mandatory E2E gateway declares closeLid high-risk; exercise its real approval too.
  if (plan.status !== 'submitted') return plan;
  await json(await request.post(`${api}/api/v1/approvals/${plan.approvalId}/decision`, {
    headers: { 'X-Actor': 'user:issue26-test-reviewer' }, data: { decision: 'approve' },
  }));
  return json<PlanDocument>(await request.get(`${api}/api/v1/plans/${plan.id}`));
}
export async function closeLid(request: APIRequestContext, sessionId: string, plan: PlanDocument) {
  const result = await json<{ status: string; invocationId: string }>(await request.post(`${api}/api/v1/sessions/${sessionId}/actions`, {
    headers, data: { requestId: `close-${plan.id}`, entityKey: centrifuge, action: 'closeLid',
      planId: plan.id, stepId: 1, planRevision: plan.revision, wait: true, timeoutSeconds: 4 },
  }));
  expect(result.status).toBe('succeeded');
  return result;
}
export async function stopSession(request: APIRequestContext, sessionId: string, info: TestInfo) {
  const response = await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  expect(response.ok(), await response.text()).toBe(true);
  const after = await json<{ state: string }>(await request.get(`${api}/api/v1/sessions/${sessionId}`));
  expect(after.state).toBe('STOPPED');
  await info.attach('session-cleanup.json', { body: JSON.stringify({ sessionId, deleteStatus: response.status(), after }), contentType: 'application/json' });
}
export async function geometry(page: Page, preview: AvailablePreview, detail = false) {
  const inspect = () => page.evaluate(detail => detail ? window.__planViewDebug?.() : window.__spatialDebug?.getPreviewGeometry(), detail);
  await page.waitForFunction(({ detail, points }) => {
    const value = detail ? window.__planViewDebug?.() : window.__spatialDebug?.getPreviewGeometry();
    return Boolean(value && value.points.length === points &&
      Object.values(value.ghosts).every(body => body.meshes > 0 && body.source !== 'loading'));
  }, { detail, points: preview.points.length }, { timeout: 10_000 });
  await expect(page.getByTestId('scene-viewport')).toHaveAttribute('data-camera-fitted', 'true');
  const actual = (await inspect())!;
  expect(actual.policyDriven).toBe(false);
  expect(actual.target).toEqual(preview.targetPose.p);
  expect(actual.targetInView, 'Actual target projects inside the rendered viewport').toBe(true);
  expect(actual.points).toHaveLength(preview.points.length);
  actual.points.forEach((point, index) => point.forEach((value, axis) => expect(value).toBeCloseTo(preview.points[index][axis], 5)));
  expect(Object.keys(actual.ghosts).sort()).toEqual(Object.keys(preview.targetBodyPoses).sort());
  for (const [key, pose] of Object.entries(preview.targetBodyPoses)) {
    const body = actual.ghosts[key];
    expect(body.p).toEqual(pose.p); expect(body.q).toEqual(pose.q);
    expect(body.meshes).toBeGreaterThan(0); expect(body.visible).toBe(true);
    expect(body.opacity.length).toBeGreaterThan(0);
    body.opacity.forEach(value => expect(value).toBe(.24));
  }
  return actual;
}
