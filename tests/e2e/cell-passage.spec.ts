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
    await controls.getByRole('button', { name: '申请浏览器控制', exact: true }).click();
    await expect(controls.getByRole('button', { name: '确认停稳交接' })).toBeEnabled();
    await controls.getByRole('button', { name: '确认停稳交接' }).click();
    await expect(controls.getByRole('status')).toContainText('已确认控制交接');
    await controls.getByRole('button', { name: '读取现场核对' }).click();
    await controls.locator('.passage-review input').check();
    await controls.getByRole('button', { name: '准备未开始步骤' }).click();
    await expect(controls.getByRole('status')).toContainText('已修订');
    let admittedPosts = 0;
    await page.route('**/api/v1/sessions/*/actions', async interception => {
      const body = interception.request().postDataJSON();
      if (body.stepId === 100001) {
        admittedPosts++;
        await interception.fetch(); // The real API admitted it; only its response is lost.
        await interception.abort('failed');
      } else await interception.continue();
    });
    await controls.getByRole('button', { name: '执行一步' }).click();
    await expect(page.getByTestId('plan-step-100001')).toHaveAttribute('data-status', 'done');
    await expect(page.getByTestId('plan-step-100002')).toHaveAttribute('data-status', 'pending');
    expect(admittedPosts).toBe(1);
    const observed: PlanDocument = await (await request.get(`${api}/api/v1/plans/${plan.id}`)).json();
    expect(observed.stepResults['100001'].invocationIds).toHaveLength(1);
    await expect(controls.getByRole('status')).toContainText('当前步骤已完成');
    await controls.getByRole('button', { name: '自动推进' }).click();
    await expect(page.getByTestId('plan-step-100002')).toHaveAttribute('data-status', 'running');
    await controls.getByRole('button', { name: '暂停', exact: true }).click();
    await expect(controls.getByRole('status')).toContainText('已暂停');
    const before = await (await request.get(route)).json();
    await controls.getByRole('button', { name: '人工接管', exact: true }).click();
    await controls.getByRole('button', { name: '取消在途调用' }).click();
    await expect(controls.getByRole('button', { name: '确认停稳交接' })).toBeEnabled();
    await controls.getByRole('button', { name: '确认停稳交接' }).click();
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
