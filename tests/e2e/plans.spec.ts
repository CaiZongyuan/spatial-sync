import { expect, test, type APIRequestContext, type Page, type TestInfo } from '@playwright/test';
import type {} from '../../apps/web/src/components/Workbench';
import type {} from '../../apps/web/src/components/PlansPage';
import type { AvailablePreview, PlanDocument } from '../../apps/web/src/lib/plans/model';
import { readSegment, type Recording } from '../../apps/web/src/lib/replay/reader';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const centrifuge = '/World/Lab/Centrifuge';
const robot = '/World/Lab/RobotA';
const headers = { 'X-Actor': 'agent:issue26-e2e' };
const condition = { entity: centrifuge, property: 'lidOpen', equals: false };
const move = (id: number, positions = [1, .5, -.5, .1, .2, .3, .4]) =>
  ({ id, do: { entity: robot, action: 'moveJoints', params: { positions } } });
const close = { id: 1, do: { entity: centrifuge, action: 'closeLid' }, expect: condition };

async function json<T>(response: Awaited<ReturnType<APIRequestContext['get']>>): Promise<T> {
  expect(response.ok(), await response.text()).toBe(true);
  return response.json() as Promise<T>;
}
async function createSession(request: APIRequestContext) {
  return json<{ sessionId: string }>(await request.post(`${api}/api/v1/sessions`, {
    data: { sceneVersion: 'lab-demo@2', source: 'synthetic', continueWithoutObservers: true },
  }));
}
async function submit(request: APIRequestContext, title: string, steps: unknown[]) {
  return approve(request, await json<PlanDocument>(await request.post(`${api}/api/v1/plans`, {
    headers, data: { title, target: 'session', steps },
  })));
}
async function approve(request: APIRequestContext, plan: PlanDocument) {
  // The mandatory E2E gateway declares closeLid high-risk; exercise its real approval too.
  if (plan.status !== 'submitted') return plan;
  await json(await request.post(`${api}/api/v1/approvals/${plan.approvalId}/decision`, {
    headers: { 'X-Actor': 'user:issue26-test-reviewer' }, data: { decision: 'approve' },
  }));
  return json<PlanDocument>(await request.get(`${api}/api/v1/plans/${plan.id}`));
}
async function closeLid(request: APIRequestContext, sessionId: string, plan: PlanDocument) {
  const result = await json<{ status: string; invocationId: string }>(await request.post(`${api}/api/v1/sessions/${sessionId}/actions`, {
    headers, data: { requestId: `close-${plan.id}`, entityKey: centrifuge, action: 'closeLid',
      planId: plan.id, stepId: 1, planRevision: plan.revision, wait: true, timeoutSeconds: 4 },
  }));
  expect(result.status).toBe('succeeded');
  return result;
}
async function stopSession(request: APIRequestContext, sessionId: string, info: TestInfo) {
  const response = await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  expect(response.ok(), await response.text()).toBe(true);
  const after = await json<{ state: string }>(await request.get(`${api}/api/v1/sessions/${sessionId}`));
  expect(after.state).toBe('STOPPED');
  await info.attach('session-cleanup.json', { body: JSON.stringify({ sessionId, deleteStatus: response.status(), after }), contentType: 'application/json' });
}
async function geometry(page: Page, preview: AvailablePreview, detail = false) {
  const inspect = () => page.evaluate(detail => detail ? window.__planViewDebug?.() : window.__spatialDebug?.getPreviewGeometry(), detail);
  await expect.poll(async () => {
    const value = await inspect();
    return value && value.points.length === preview.points.length &&
      Object.values(value.ghosts).every(body => body.meshes > 0 && body.source !== 'loading');
  }).toBe(true);
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
async function seek(page: Page, timeMs: number) {
  await page.getByLabel('Replay position').evaluate((element, value) => {
    (element as HTMLInputElement).value = String(value > 0 ? value - 1 : 1);
  }, timeMs);
  await page.getByLabel('Replay position').press(timeMs > 0 ? 'ArrowRight' : 'ArrowLeft');
  await expect(page.getByLabel('Replay position')).toHaveValue(String(timeMs));
}

test('real operation plan tracks calls and revisions beside an actual TCP path and ghost bodies', async ({ page, request, context }, info) => {
  const { sessionId } = await createSession(request);
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  try {
    const steps = [close, { id: 2, wait: condition, timeoutSeconds: 2 }, { id: 3, check: condition }, move(4)];
    const plan = await submit(request, 'Browser: close, verify and move', steps);
    await page.goto(`/sessions/${sessionId}?camera=isometric&plan=${plan.id}&step=4`);
    await expect(page.getByTestId('connection-status')).toHaveText('connected');
    const panel = page.getByTestId('plan-panel');
    await expect(panel).toContainText('agent · issue26-e2e');
    await expect(panel).toContainText(plan.approvalMode === 'auto' ? '自动批准' : '人工审批');
    await expect(page.getByTestId('plan-preview-status')).toContainText('策略预览尚未接入');
    const preview = await json<AvailablePreview>(await request.get(`${api}/api/v1/plans/${plan.id}/steps/4/preview?revision=1`));
    const firstGeometry = await geometry(page, preview);
    await info.attach('initial-rendered-preview.json', { body: JSON.stringify({ preview, rendered: firstGeometry }), contentType: 'application/json' });
    const liveMaterials = await page.evaluate(keys => Object.fromEntries(keys.map(key =>
      [key, window.__spatialDebug?.inspectVisuals(key)?.materialOpacity])), Object.keys(preview.targetBodyPoses));
    for (const values of Object.values(liveMaterials)) {
      expect(values?.length).toBeGreaterThan(0);
      expect(values).not.toContain(.24);
    }
    await expect.poll(async () => (await page.evaluate(() => window.__spatialDebug?.getPreviewGeometry()))?.dashOffset).not.toBe(firstGeometry.dashOffset);
    const invocation = await closeLid(request, sessionId, plan);
    await expect(page.getByTestId('plan-step-1')).toHaveAttribute('data-status', 'done');
    await page.getByTestId('plan-step-1').getByText('关联调用 · 1').click();
    await expect(page.getByTestId('plan-step-1')).toContainText(invocation.invocationId);
    const ref = { planId: plan.id, planRevision: 1 };
    const waited = await json<{ satisfied: boolean }>(await request.get(`${api}/api/v1/sessions/${sessionId}/wait`, {
      headers, params: { key: centrifuge, property: 'lidOpen', op: 'equals', value: 'false', ...ref, stepId: 2 },
    }));
    expect(waited.satisfied).toBe(true);
    const checked = await json<{ satisfied: boolean }>(await request.post(`${api}/api/v1/sessions/${sessionId}/check`, {
      headers, data: { key: centrifuge, property: 'lidOpen', op: 'equals', value: false, ...ref, stepId: 3 },
    }));
    expect(checked.satisfied).toBe(true);
    for (const id of [2, 3]) await expect(page.getByTestId(`plan-step-${id}`)).toHaveAttribute('data-status', 'done');
    const revised = await approve(request, await json<PlanDocument>(await request.post(`${api}/api/v1/plans/${plan.id}/revisions`, {
      headers, data: { title: plan.title, target: 'session', steps: [...steps.slice(0, 3), move(4, [.2, .4, -.5, 0, 0, 0, 0]), { id: 5, check: condition }] },
    })));
    await expect(panel).toContainText('3 / 5 步已完成');
    await expect(page.getByTestId('plan-revision-diff')).toContainText('修改步骤 4；新增步骤 5');
    await expect(page.getByTestId('plan-step-5')).toContainText('r2 新增');
    const updated = await json<AvailablePreview>(await request.get(`${api}/api/v1/plans/${plan.id}/steps/4/preview?revision=2`));
    await geometry(page, updated);
    await page.getByTestId('plan-step-5').getByRole('button').focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('plan-command')).toContainText(`--step 5 --plan-revision ${revised.revision}`);
    await expect(page.getByTestId('plan-preview-status')).toContainText('等待与检查步骤不产生机械臂路径');
    await expect.poll(() => page.evaluate(() => window.__spatialDebug?.getPreviewGeometry())).toBeUndefined();
    expect(await page.evaluate(keys => Object.fromEntries(keys.map(key =>
      [key, window.__spatialDebug?.inspectVisuals(key)?.materialOpacity])), Object.keys(preview.targetBodyPoses))).toEqual(liveMaterials);
    await page.getByTestId('plan-step-1').getByRole('button').click();
    await expect(page.getByTestId('plan-preview-status')).toContainText('此动作未提供机械臂路径预览');
    // Original scene tree / Inspector interaction remains available beside the checklist.
    await page.getByTestId(`entity-${centrifuge}`).click();
    await expect(page.getByTestId('property-lidOpen').locator('output')).toHaveText('false');
    await page.goto(`/plans/${plan.id}?step=4`);
    await expect(page.getByRole('table')).toContainText(plan.id);
    await expect(page.getByRole('table')).toContainText('issue26-e2e');
    const detailGeometry = await geometry(page, updated, true);
    await info.attach('detail-rendered-preview.json', { body: JSON.stringify({ preview: updated, rendered: detailGeometry }), contentType: 'application/json' });
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.getByRole('button', { name: '复制', exact: true }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await page.getByTestId('plan-command').textContent());
    for (const theme of ['dark', 'light']) {
      await page.getByLabel('Color theme').selectOption(theme);
      await page.screenshot({ path: info.outputPath(`plans-${theme}-1440.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 400, height: 960 });
    await page.screenshot({ path: info.outputPath('plans-light-400.png'), fullPage: true });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const reduced = await geometry(page, updated, true);
    const offsets = await page.evaluate(async () => {
      const values = [];
      for (let frame = 0; frame < 4; frame++) {
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
        values.push(window.__planViewDebug?.()?.dashOffset);
      }
      return values;
    });
    expect(offsets).toEqual(Array(4).fill(reduced.dashOffset));
    expect(errors).toEqual([]);
    await info.attach('rendered-preview.json', { body: JSON.stringify({ preview: updated, rendered: reduced }), contentType: 'application/json' });
  } finally { await stopSession(request, sessionId, info); }
});

test('actual recorded plans rebuild on backward seek and scene rollover without any live API or socket', async ({ page, request, baseURL }, info) => {
  const { sessionId } = await createSession(request);
  let closed = false;
  try {
    const first = await submit(request, 'Recorded first scene', [close, move(2)]);
    await closeLid(request, sessionId, first);
    await approve(request, await json<PlanDocument>(await request.post(`${api}/api/v1/plans/${first.id}/revisions`, {
      headers, data: { title: first.title, target: 'session', steps: [close, move(2, [.2, .4, -.5, 0, 0, 0, 0])] },
    })));
    await request.get(`${api}/api/v1/sessions/${sessionId}/state`);
    await json(await request.post(`${api}/api/v1/sessions/${sessionId}/reload`));
    const second = await submit(request, 'Recorded replacement scene', [move(1)]);
    await stopSession(request, sessionId, info); closed = true;
    const recordings = await json<(Recording & { url: string })[]>(await request.get(`${api}/api/v1/recordings`));
    const recording = recordings.find(value => value.sessionId === sessionId)!;
    expect(recording.status).toBe('complete'); expect(recording.segments).toHaveLength(2);
    const records = await Promise.all(recording.segments.map(async segment => {
      const response = await request.get(`${api}/recordings/${sessionId}/${segment.file}`);
      expect(response.ok()).toBe(true);
      const bytes = await response.body();
      return readSegment(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer).records;
    }));
    const updates = records.flat().filter(record => record.message?.type === 'plan.update' && record.message.update === 'full');
    const at = (id: string, revision: number) => {
      const record = updates.find(record => record.message?.type === 'plan.update' && record.message.update === 'full' &&
        record.message.plan.id === id && record.message.plan.revision === revision)!;
      expect(record).toBeTruthy();
      return Math.ceil(record.atMs - recording.segments[0].startSampleTimeMs);
    };
    const times = { first: at(first.id, 1), revised: at(first.id, 2), replacement: at(second.id, 1) };
    const live: string[] = [];
    page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) live.push(request.url()); });
    page.on('websocket', socket => { if (new URL(socket.url()).pathname.startsWith('/api/')) live.push(socket.url()); });
    await page.route('**/api/**', route => route.abort());
    const src = new URL(recording.url, baseURL).href;
    await page.goto(`/replay/${sessionId}?src=${encodeURIComponent(src)}`);
    await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
    const panel = page.getByTestId('plan-panel');
    await seek(page, times.first);
    await expect(panel).toContainText(first.title);
    await expect(page.getByTestId('plan-step-1')).toHaveAttribute('data-status', 'pending');
    await expect(panel).toContainText('录制未包含');
    await seek(page, times.revised);
    await expect(page.getByTestId('plan-step-1')).toHaveAttribute('data-status', 'done');
    await expect(page.getByTestId('plan-revision-diff')).toContainText('r1 → r2');
    await expect(page.getByTestId('plan-step-1')).toContainText('关联调用 · 1');
    await seek(page, times.replacement);
    await expect(panel).toContainText(second.title); await expect(panel).not.toContainText(first.title);
    await expect(page.getByTestId('plan-step-1')).toHaveAttribute('data-status', 'pending');
    await seek(page, times.first);
    await expect(panel).toContainText(first.title);
    await expect(page.getByTestId('plan-step-1')).toHaveAttribute('data-status', 'pending');
    await expect(page.getByTestId('plan-step-1').getByText('关联调用 · 1')).toHaveCount(0);
    await expect(page.getByTestId('plan-preview-status')).toContainText('录制未包含路径预览');
    await expect(page.getByTestId('approval-pending-badge')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Close session', exact: true })).toBeDisabled();
    expect(live).toEqual([]);
    await info.attach('recorded-plan-times.json', { body: JSON.stringify({ sessionId, times, segments: recording.segments, live }), contentType: 'application/json' });
  } finally { if (!closed) await stopSession(request, sessionId, info); }
});

test('disclosed policy preview contract fixture draws only the actual target', async ({ page, request }, info) => {
  const { sessionId } = await createSession(request);
  try {
    const plan = await submit(request, 'Policy target contract fixture', [move(1)]);
    const actual = await json<AvailablePreview>(await request.get(`${api}/api/v1/plans/${plan.id}/steps/1/preview?revision=1`));
    const fixture: AvailablePreview = { ...actual, policyDriven: true, points: [], targetBodyPoses: {} };
    await page.route(`**/api/v1/plans/${plan.id}/steps/1/preview?*`, route => route.fulfill({ json: fixture }));
    await page.goto(`/plans/${plan.id}`);
    await expect(page.getByRole('button', { name: '策略', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('plan-preview-status')).toContainText('当前程序仅提供目标位姿');
    await expect(page.getByTestId('plan-preview-figure')).toContainText('由策略执行 · 仅显示目标');
    await expect.poll(() => page.evaluate(() => window.__planViewDebug?.())).toMatchObject({
      policyDriven: true, target: actual.targetPose.p, points: [], ghosts: {}, targetInView: true,
    });
    await info.attach('disclosed-policy-contract-fixture.json', { body: JSON.stringify(fixture), contentType: 'application/json' });
  } finally { await stopSession(request, sessionId, info); }
});
