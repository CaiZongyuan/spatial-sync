import { expect, test } from '@playwright/test';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const centrifuge = '/World/Lab/Centrifuge';

test('approval inbox renders real empty state and shares its navigation badge', async ({ page }) => {
  await page.goto('/approvals');
  await expect(page.getByRole('heading', { name: '待审批', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '待审批收件箱' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '具身层阶段' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Approvals', exact: true })).toHaveAttribute('aria-current', 'page');
});

test('high risk invocation enters inbox, approved work can be revoked before start, then another approval executes', async ({ page, request }, info) => {
  test.skip(process.env.SPATIAL_TEST_APPROVAL !== '1', 'Requires the temporary high risk definition fixture');
  const created = await request.post(`${api}/api/v1/sessions`, { data: {
    sceneId: 'lab-demo', source: 'synthetic', approval: { high: 'require' }, continueWithoutObservers: true,
  } });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  const route = `${api}/api/v1/sessions/${sessionId}`;
  const command = (name: string) => request.post(`${route}/commands`, { data: {
    type: 'command.request', requestId: name, command: name,
  } });
  const invoke = (requestId: string) => request.post(`${route}/actions`, {
    headers: { 'X-Actor': 'agent:lab-operator' },
    data: { requestId, entityKey: centrifuge, action: 'closeLid' },
  });
  try {
    expect((await command('pause')).ok()).toBe(true);
    const first = await (await invoke('inbox-revoke')).json();
    expect(first.status).toBe('pending_approval');
    await page.goto('/approvals');
    const inbox = page.getByRole('region', { name: '待审批收件箱' });
    const firstCard = page.getByRole('article').filter({ hasText: first.invocationId });
    await expect(inbox).toContainText('agent:lab-operator');
    await expect(page.getByTestId('approval-pending-badge')).toHaveText('1');
    await firstCard.getByLabel('备注').fill('现场已确认，可以进入执行队列');
    await firstCard.getByRole('button', { name: '批准', exact: true }).click();
    await expect(firstCard).toContainText('已批准');
    await expect(page.getByTestId('approval-pending-badge')).toHaveCount(0);
    await firstCard.getByLabel('备注').fill('执行前补充检查，撤销本次批准');
    await firstCard.getByRole('button', { name: '撤销', exact: true }).click();
    await expect(firstCard).toContainText('已撤销');
    const cancelled = await (await request.get(`${route}/invocations/${first.invocationId}`)).json();
    expect(cancelled.status).toBe('cancelled');
    expect(cancelled.startedSimTime).toBeNull();
    const state = await (await request.get(`${route}/state`, { params: { keys: centrifuge } })).json();
    expect(state.entities[centrifuge].values.lidOpen).toBe(true);

    const second = await (await invoke('inbox-execute')).json();
    const secondCard = page.getByRole('article').filter({ hasText: second.invocationId });
    await expect(secondCard.getByRole('button', { name: '批准', exact: true })).toBeVisible();
    await secondCard.getByLabel('备注').fill('补充检查完成');
    await secondCard.getByRole('button', { name: '批准', exact: true }).click();
    await expect(secondCard).toContainText('已批准');
    expect((await command('resume')).ok()).toBe(true);
    const done = await (await request.get(`${route}/invocations/${second.invocationId}`, {
      params: { wait: true, timeoutSeconds: 5 },
    })).json();
    expect(done.status).toBe('succeeded');
    await expect(secondCard.getByRole('button', { name: '撤销', exact: true })).toHaveCount(0);
    await expect(page.getByRole('complementary', { name: '活动时间线' })).toContainText('补充检查完成');
    await page.screenshot({ path: info.outputPath('approval-audit.png'), fullPage: true });
    const records = (await (await request.get(`${api}/api/v1/approvals`)).json()).approvals;
    const audit = records.find((item: { subjectId: string }) => item.subjectId === first.invocationId);
    expect(audit.decisions.map((item: { decision: string }) => item.decision)).toEqual(['approve', 'revoke']);
    expect(audit.decisions[0].actor).toEqual({ kind: 'user', name: 'web' });
  } finally { await request.delete(route); }
});

test('read-only observer sees operator decisions and cannot approve or revoke', async ({ browser, request, baseURL }) => {
  test.skip(process.env.SPATIAL_TEST_APPROVAL_AUTH !== '1', 'Requires the owned remote-token fixture');
  const operatorHeaders = { Authorization: 'Bearer issue6-test-operator' };
  const viewerHeaders = { Authorization: 'Bearer issue6-test-viewer' };
  const created = await request.post(`${api}/api/v1/sessions`, { headers: operatorHeaders, data: {
    sceneId: 'lab-demo', source: 'synthetic', approval: { high: 'require' }, continueWithoutObservers: true,
  } });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  const route = `${api}/api/v1/sessions/${sessionId}`;
  const operator = await browser.newContext({ baseURL, extraHTTPHeaders: operatorHeaders });
  const viewer = await browser.newContext({ baseURL, extraHTTPHeaders: viewerHeaders });
  const human = await operator.newPage();
  const observer = await viewer.newPage();
  try {
    const paused = await request.post(`${route}/commands`, { headers: operatorHeaders, data: {
      type: 'command.request', requestId: 'auth-pause', command: 'pause',
    } });
    expect(paused.ok()).toBe(true);
    const invocation = await (await request.post(`${route}/actions`, { headers: operatorHeaders, data: {
      requestId: 'auth-approval', entityKey: centrifuge, action: 'closeLid',
    } })).json();
    await human.goto('/approvals');
    await observer.goto('/approvals');
    const humanCard = human.getByRole('article').filter({ hasText: invocation.invocationId });
    const observerCard = observer.getByRole('article').filter({ hasText: invocation.invocationId });
    await expect(observer.getByRole('status')).toContainText('只读访问');
    await expect(observerCard.getByRole('button', { name: '批准', exact: true })).toBeDisabled();
    await expect(observerCard.getByRole('button', { name: '驳回', exact: true })).toBeDisabled();
    await expect(observerCard.getByLabel('备注')).toBeDisabled();
    await expect(observer.getByTestId('approval-pending-badge')).toHaveText('1');
    await humanCard.getByLabel('备注').fill('操作员确认，观察者同步可见');
    await humanCard.getByRole('button', { name: '批准', exact: true }).click();
    await expect(observerCard).toContainText('已批准');
    await expect(observerCard).toContainText('操作员确认，观察者同步可见');
    await expect(observerCard.getByRole('button', { name: '撤销', exact: true })).toBeDisabled();
    await expect(observer.getByTestId('approval-pending-badge')).toHaveCount(0);
    await humanCard.getByRole('button', { name: '撤销', exact: true }).click();
    await expect(observerCard).toContainText('已撤销');
    const value = await (await request.get(`${route}/invocations/${invocation.invocationId}`, { headers: operatorHeaders })).json();
    expect(value.status).toBe('cancelled');
    expect(value.startedSimTime).toBeNull();
  } finally {
    await operator.close(); await viewer.close();
    await request.delete(route, { headers: operatorHeaders });
  }
});
