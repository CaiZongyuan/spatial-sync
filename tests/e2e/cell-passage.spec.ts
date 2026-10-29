import { expect, test } from '@playwright/test';
import type { PlanDocument } from '../../apps/web/src/lib/plans/model';

// The dedicated public Host fixture is Synthetic, never Newton qualification.
test.skip(process.env.SPATIAL_CELL_PASSAGE_E2E !== '1', 'Requires the owned cell_passage_gateway fixture');
const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8024';

test('passage Web submits once, fences takeover, freezes time and replays without live controls', async ({ page, request }, info) => {
  const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', continueWithoutObservers: true } });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  let stopped = false;
  const route = `${api}/api/v1/sessions/${sessionId}`;
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  try {
    const submitted = await request.post(`${api}/api/v1/plans`, { data: { title: 'Synthetic Host browser smoke', target: 'session', sessionId,
      steps: [1, 20].map((seconds, index) => ({ id: 100001 + index, do: { entity: '/workcell', action: 'wait', params: {
        owner: 'auto', epoch: 0, operationId: `fixture-wait-${index}`, seconds,
      } } })) } });
    expect(submitted.ok()).toBe(true);
    const plan: PlanDocument = await submitted.json();
    await page.goto(`/sessions/${sessionId}?plan=${plan.id}`);
    await expect(page.getByTestId('connection-status')).toHaveAttribute('data-connection', 'connected');
    const controls = page.getByRole('region', { name: '传代控制' });
    let admittedPosts = 0;
    await page.route('**/api/v1/sessions/*/actions', async interception => {
      const body = interception.request().postDataJSON();
      if (body.stepId === 100001) {
        admittedPosts++;
        await interception.fetch(); // The real API admitted it; only its response is lost.
        await interception.abort('failed');
      } else await interception.continue();
    });
    await controls.getByRole('button', { name: '单步', exact: true }).click();
    await expect(page.getByTestId('plan-step-100001')).toHaveAttribute('data-status', 'done');
    await expect(page.getByTestId('plan-step-100002')).toHaveAttribute('data-status', 'pending');
    expect(admittedPosts).toBe(1);
    const observed: PlanDocument = await (await request.get(`${api}/api/v1/plans/${plan.id}`)).json();
    expect(observed.stepResults['100001'].invocationIds).toHaveLength(1);
    await expect(controls.getByRole('status')).toContainText('当前步骤已完成');
    await expect(controls.locator('input[type=checkbox]:visible')).toHaveCount(0);
    await controls.getByRole('button', { name: '运行', exact: true }).click();
    await expect(page.getByTestId('plan-step-100002')).toHaveAttribute('data-status', 'running');
    await controls.getByRole('button', { name: '暂停', exact: true }).click();
    await expect(controls.getByRole('status')).toContainText('已暂停');
    const before = await (await request.get(route)).json();
    await controls.getByRole('button', { name: '接管', exact: true }).click();
    await expect.poll(async () => (await (await request.get(`${route}/state?keys=%2Fworkcell`)).json()).entities['/workcell'].values.controlMode).toBe('manual');
    const after = await (await request.get(route)).json();
    expect(after.simTime).toBe(before.simTime);
    const values = (await (await request.get(`${route}/state?keys=%2Fworkcell`)).json()).entities['/workcell'].values;
    expect(values.controlMode).toBe('manual'); expect(values.controlSafe).toBe(true);
    const stale = await request.post(`${route}/actions`, { data: { requestId: 'late-old-client', entityKey: '/workcell', action: 'wait',
      params: { owner: 'auto', epoch: 0, operationId: 'late-old-client', seconds: 1 } } });
    expect(stale.status()).toBe(409); expect((await stale.json()).error.code).toBe('stale_control');
    await page.getByRole('button', { name: '全屏', exact: true }).click();
    await expect(page.getByRole('button', { name: '退出全屏', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: '全屏', exact: true })).toBeVisible();
    await info.attach('synthetic-host-controls.png', { body: await page.screenshot(), contentType: 'image/png' });
    expect((await request.delete(route)).ok()).toBe(true); stopped = true;
    const recordings = await (await request.get(`${api}/api/v1/recordings`)).json();
    const recording = recordings.find((row: { sessionId: string }) => row.sessionId === sessionId);
    expect(recording).toBeTruthy();
    await page.goto(`/replay/${sessionId}?src=${encodeURIComponent(new URL(recording.url, 'http://127.0.0.1:5224').href)}`);
    await expect(page.getByLabel('Replay position')).toBeVisible();
    await expect(page.getByRole('region', { name: '传代控制' })).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    if (!stopped) await request.delete(route);
  }
});

test('failed fractional intent keeps its original resolved goal and resumes only an explicit literal remainder', async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', seed: 77, continueWithoutObservers: true } });
  expect(created.ok()).toBe(true); const { sessionId } = await created.json(); const route = `${api}/api/v1/sessions/${sessionId}`;
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  try {
    const actions = [
      { action: 'reserve', params: { resource: '/peer', device: '/workcell' } },
      { action: 'pickupTip', params: { tip: 'T1' } },
      { action: 'aspirateFraction', params: { vessel: 'source', fraction: .5 } },
    ];
    const submitted = await request.post(`${api}/api/v1/plans`, { data: { title: 'Synthetic resolved fraction recovery', sessionId, target: 'session',
      steps: actions.map((action, index) => ({ id: 200001 + index, do: { entity: '/workcell', action: action.action,
        params: { ...action.params, owner: 'auto', epoch: 0, operationId: `fraction-${index}` } } })) } });
    expect(submitted.ok()).toBe(true); const plan: PlanDocument = await submitted.json();
    const posts: { action: string; params: Record<string, unknown> }[] = [];
    page.on('request', message => { if (message.method() === 'POST' && message.url().endsWith('/actions')) { const body = message.postDataJSON(); if (body.planId) posts.push(body); } });
    await page.goto(`/sessions/${sessionId}?plan=${plan.id}`);
    await expect(page.getByTestId('connection-status')).toHaveAttribute('data-connection', 'connected');
    const controls = page.getByRole('region', { name: '传代控制' });
    await controls.getByRole('button', { name: '运行', exact: true }).click();
    await expect(page.getByTestId('plan-step-200003')).toHaveAttribute('data-status', 'failed', { timeout: 20_000 });
    const state = async () => (await (await request.get(`${route}/state?keys=%2Fworkcell`)).json()).entities['/workcell'].values;
    const failed = await state();
    const receipt = failed.materialSnapshot.operations.find((row: { operationId: string }) => row.operationId === 'fraction-2');
    expect(receipt.requestedUl).toBe(250); expect(receipt.resolvedUl).toBeCloseTo(250, 2); expect(receipt.appliedUl).toBeCloseTo(40, 5); expect(receipt.remainingUl).toBeCloseTo(210, 2);
    expect(failed.materialSnapshot.vessels[0].volumeUl).toBeCloseTo(460, 5); expect(failed.materialSnapshot.tip.volumeUl).toBeCloseTo(40, 5);
    await controls.getByRole('button', { name: '接管', exact: true }).click();
    await expect.poll(async () => (await state()).controlMode).toBe('manual');
    await controls.getByRole('button', { name: '继续', exact: true }).click();
    await expect(controls.getByRole('heading', { name: '续行前核对' })).toBeVisible();
    const form = controls.locator('fieldset').filter({ hasText: '200003' });
    await expect(form).toContainText('原解析目标 250'); await expect(form).toContainText('已实转 40');
    await form.getByRole('button', { name: /^采用原解析余量/ }).click();
    const precise = form.getByLabel('后续操作量（µL）');
    await expect(precise).not.toBeVisible();
    expect(Number(await precise.inputValue())).toBe(receipt.remainingUl);
    await form.getByRole('checkbox', { name: /已核对原目标/ }).check();
    await info.attach('fraction-40-remainder-review.png', { body: await page.screenshot(), contentType: 'image/png' });
    await controls.getByRole('button', { name: '继续', exact: true }).click();
    await expect(page.getByTestId('plan-step-200003')).toHaveAttribute('data-status', 'done', { timeout: 20_000 });
    const after = await state();
    expect(posts.filter(row => row.action === 'aspirateFraction')).toHaveLength(1);
    const repair = posts.filter(row => row.action === 'aspirate'); expect(repair).toHaveLength(1);
    expect(Number(repair[0].params.volumeUl)).toBeCloseTo(210, 2); expect(repair[0].params.operationId).not.toBe('fraction-2');
    expect(repair[0].params).not.toHaveProperty('fraction');
    expect(after.materialSnapshot.vessels[0].volumeUl).toBeCloseTo(250, 2); expect(after.materialSnapshot.tip.volumeUl).toBeCloseTo(250, 2);
    expect(after.materialSnapshot.operations.find((row: { operationId: string }) => row.operationId === 'fraction-2').appliedUl).toBeCloseTo(40, 5);
    expect(errors).toEqual([]);
  } finally { await request.delete(route); }
});

for (const seed of [77, 78]) test(`mid-run takeover uses declared configuration totals and ${seed === 77 ? 'retains a distinct fresh-sample status' : 'rejects a short producer'}`, async ({ page, request }, info) => {
  test.setTimeout(60_000);
  const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', seed, continueWithoutObservers: true } });
  expect(created.ok()).toBe(true); const { sessionId } = await created.json(), route = `${api}/api/v1/sessions/${sessionId}`;
  try {
    for (const [index, action] of [
      { action: 'reserve', params: { resource: '/peer', device: '/workcell' } },
      { action: 'pickupTip', params: { tip: 'T1' } },
      { action: 'aspirateFraction', params: { vessel: 'source', fraction: .5 } },
    ].entries()) {
      const response = await request.post(`${route}/actions`, { data: { requestId: `external-${index}`, entityKey: '/workcell', ...action,
        params: { ...action.params, owner: 'auto', epoch: 0, operationId: `external-${index}` }, wait: true, timeoutSeconds: 3 } });
      expect(response.ok()).toBe(true); expect((await response.json()).status).toBe(index === 2 ? 'failed' : 'succeeded');
    }
    const source = async () => (await (await request.get(`${route}/state?keys=%2Fworkcell`)).json()).entities['/workcell'].values;
    const before = await source(); expect(before.materialSnapshot.tip.volumeUl).toBeCloseTo(40, 5);
    const prepared = await request.post(`${api}/api/v1/plans`, { data: { title: 'Mid-run browser checkpoint', target: 'session', sessionId,
      steps: [{ id: 100001, do: { entity: '/workcell', action: 'wait', params: { owner: 'auto', epoch: 0, operationId: 'future-wait', seconds: 1 } } }] } });
    expect(prepared.ok()).toBe(true); const plan = await prepared.json();
    await page.goto(`/sessions/${sessionId}?plan=${plan.id}`);
    await expect(page.getByTestId('connection-status')).toHaveAttribute('data-connection', 'connected');
    const controls = page.getByRole('region', { name: '传代控制' });
    await controls.getByRole('button', { name: '单步', exact: true }).click();
    await expect(page.getByTestId('passage-initial-basis')).toContainText('固定配方配置预期 · 总液量 500');
    await expect(page.getByTestId('passage-initial-basis')).toContainText('未观察到，中途状态不充当原始初态');
    if (seed === 77) await expect(controls.getByRole('heading', { name: '续行前核对' })).toBeVisible();
    else await expect(controls.getByRole('status')).toContainText('与配置预期守恒不符');
    const after = await source(); expect(after.operationCount).toBe(before.operationCount);
    expect(after.materialSnapshot.vessels[0].volumeUl).toBe(before.materialSnapshot.vessels[0].volumeUl);
    expect(after.materialSnapshot.tip.volumeUl).toBe(before.materialSnapshot.tip.volumeUl);
    await expect(page.getByTestId('plan-step-100001')).toHaveAttribute('data-status', 'pending');
    const visible = await controls.locator('[aria-label="主要传代操作"] button').evaluateAll(buttons => buttons.every(button => {
      const bounds = button.getBoundingClientRect(); return bounds.top >= 0 && bounds.bottom < innerHeight;
    }));
    expect(visible).toBe(true);
    await info.attach(`config-expected-mid-${seed}.png`, { body: await page.screenshot(), contentType: 'image/png' });
  } finally { await request.delete(route); }
});

test('cell passage physical toolbar cannot bypass the fenced experiment pause or keyboard gate', async ({ page, request }) => {
  const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', seed: 99, realtimeFactor: .1 } });
  expect(created.ok()).toBe(true); const { sessionId } = await created.json(), route = `${api}/api/v1/sessions/${sessionId}`;
  try {
    await request.post(`${route}/commands`, { data: { requestId: 'fixture-freeze', command: 'pause' } });
    const prepared = await request.post(`${api}/api/v1/plans`, { data: { title: 'Paused safety entrypoint', sessionId, target: 'session',
      steps: [{ id: 100001, do: { entity: '/workcell', action: 'wait', params: { owner: 'auto', epoch: 0, operationId: 'future', seconds: 1 } } }] } });
    expect(prepared.ok()).toBe(true); const plan = await prepared.json();
    const physical: string[] = [];
    page.on('websocket', socket => socket.on('framesent', frame => {
      if (typeof frame.payload !== 'string') return;
      const body = JSON.parse(frame.payload); if (body.type === 'command' && ['pause', 'resume', 'step', 'reset'].includes(body.command)) physical.push(body.command);
    }));
    await page.goto(`/sessions/${sessionId}?plan=${plan.id}`);
    await expect(page.getByRole('region', { name: '细胞传代工作台' })).toBeVisible();
    for (const name of ['Resume simulation', 'Step simulation', 'Reset simulation', 'Reload scene']) {
      const button = page.getByRole('button', { name, exact: true }); await expect(button).toBeDisabled();
      await button.evaluate(button => (button as HTMLButtonElement).click());
    }
    const state = async () => (await (await request.get(`${route}/state?keys=%2Fworkcell`)).json()).entities['/workcell'].values;
    const before = await state(), time = (await (await request.get(route)).json()).simTime;
    await page.getByRole('region', { name: '传代控制' }).getByRole('button', { name: '暂停', exact: true }).click();
    await expect.poll(async () => (await state()).controlEpoch).toBeGreaterThan(before.controlEpoch);
    const stale = await request.post(`${route}/actions`, { data: { requestId: 'late-before-takeover', entityKey: '/workcell', action: 'wait',
      params: { owner: before.controlOwner, epoch: before.controlEpoch, operationId: 'late-before-takeover', seconds: 1 } } });
    expect(stale.status()).toBe(409); expect((await stale.json()).error.code).toBe('stale_control');
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await page.keyboard.press('Space'); await page.keyboard.press('.');
    expect((await (await request.get(route)).json()).simTime).toBe(time); expect(physical).toEqual([]);
  } finally { await request.delete(route); }
});
