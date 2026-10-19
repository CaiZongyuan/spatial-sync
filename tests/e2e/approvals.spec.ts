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
