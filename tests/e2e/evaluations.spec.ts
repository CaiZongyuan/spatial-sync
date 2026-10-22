import { expect, test } from '@playwright/test';
import type {} from '../../apps/web/src/components/Workbench';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('evaluation owns a read-only workspace while explicit scene launches cancel or start afterward', async ({ page, request, context }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let manual: string | undefined;
  let evaluation: string | undefined;
  let keptRequest: string | undefined;
  let startedSession: string | undefined;
  const observer = await context.newPage();
  observer.on('pageerror', error => errors.push(error.message));
  try {
    const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneVersion: 'lab-demo@3', source: 'synthetic' } });
    expect(created.status(), await created.text()).toBe(201);
    manual = (await created.json()).sessionId;
    const submitted = await request.post(`${api}/api/v1/evaluations`, { data: { subject: 'robot_arm.virtual', suite: 'suite/lab-demo-pick-place@1' } });
    expect(submitted.status(), await submitted.text()).toBe(202);
    evaluation = (await submitted.json()).evaluationId;
    await page.goto('/scenes/lab-demo/3');
    const firstTicket = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/sessions' && response.status() === 202);
    await page.getByRole('button', { name: '启动会话' }).click();
    const cancelledRequest = (await (await firstTicket).json()).requestId;
    await expect(page.getByText('评估占用会话，启动请求已排队。')).toBeVisible();
    await page.getByRole('button', { name: '取消排队' }).click();
    await expect(page.getByText('启动请求已取消。')).toBeVisible();
    expect((await (await request.get(`${api}/api/v1/session-requests/${cancelledRequest}`)).json()).status).toBe('cancelled');
    const secondTicket = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/sessions' && response.status() === 202);
    await page.getByRole('button', { name: '启动会话' }).click();
    keptRequest = (await (await secondTicket).json()).requestId;
    await expect(page.getByRole('button', { name: '取消排队' })).toBeVisible();
    await info.attach('explicit-launch-queue.png', { body: await page.screenshot(), contentType: 'image/png' });
    expect((await request.delete(`${api}/api/v1/sessions/${manual}`)).status()).toBe(204);
    manual = undefined;
    let episodeSession: string | undefined;
    await expect.poll(async () => {
      const health = await (await request.get(`${api}/api/v1/health`)).json();
      episodeSession = health.sessions.find((value: { evaluationId?: string; state: string }) => value.evaluationId === evaluation && value.state === 'RUNNING')?.sessionId;
      return Boolean(episodeSession);
    }).toBe(true);
    await observer.goto(`/sessions/${episodeSession}`);
    await expect(observer.getByTestId('connection-status')).toHaveAttribute('data-connection', 'connected');
    await expect(observer.getByText('评估中 · 只读', { exact: true })).toBeVisible();
    for (const name of ['Pause simulation', 'Step simulation', 'Reset simulation', 'Reload scene', 'Close session'])
      await expect(observer.getByRole('button', { name, exact: true })).toBeDisabled();
    await expect(observer.getByRole('button', { name: 'Open Newton viewer', exact: true })).toHaveCount(0);
    await expect.poll(() => observer.evaluate(() => Boolean(window.__spatialDebug?.getRenderedPose('/World/Lab/Tubes/Tube01')))).toBe(true);
    await info.attach('evaluation-readonly.png', { body: await observer.screenshot(), contentType: 'image/png' });
    await observer.getByRole('button', { name: 'Session device telemetry', exact: true }).click();
    const drawer = observer.getByRole('complementary', { name: '设备抽屉' });
    await drawer.getByRole('button', { name: /Incubator/ }).click();
    await drawer.getByTestId('action-setSetpoint').locator('summary').click();
    await expect(drawer.getByRole('button', { name: 'Run setSetpoint', exact: true })).toBeDisabled();
    await info.attach('evaluation-devices-readonly.png', { body: await observer.screenshot(), contentType: 'image/png' });
    await observer.getByRole('button', { name: 'Session device telemetry', exact: true }).click();
    await expect.poll(async () => (await (await request.get(`${api}/api/v1/evaluations/${evaluation}`)).json()).status, { timeout: 35_000 }).toBe('completed');
    await expect(page).toHaveURL(/\/sessions\/session-/);
    const ticket = await (await request.get(`${api}/api/v1/session-requests/${keptRequest}`)).json();
    expect(ticket.status).toBe('started');
    startedSession = ticket.sessionId;
    expect(new URL(page.url()).pathname).toBe(`/sessions/${startedSession}`);
    await expect(page.getByRole('button', { name: 'Pause simulation', exact: true })).toBeEnabled();
    await expect.poll(() => page.evaluate(() => Boolean(window.__spatialDebug?.getRenderedPose('/World/Lab/Tubes/Tube01')))).toBe(true);
    expect((await (await request.get(`${api}/api/v1/session-requests/${cancelledRequest}`)).json()).status).toBe('cancelled');
    await info.attach('started-interactive-workspace.png', { body: await page.screenshot(), contentType: 'image/png' });
    expect(errors).toEqual([]);
  } finally {
    await observer.close();
    if (keptRequest) {
      const ticket = await (await request.delete(`${api}/api/v1/session-requests/${keptRequest}`)).json();
      startedSession ??= ticket.sessionId;
    }
    if (evaluation) await request.post(`${api}/api/v1/evaluations/${evaluation}/cancel`);
    for (const sessionId of [manual, startedSession].filter(Boolean))
      expect((await request.delete(`${api}/api/v1/sessions/${sessionId}`)).status()).toBe(204);
  }
});

test('leaving before a queued launch response releases only the session that its ticket started', async ({ page }) => {
  let release!: () => void;
  let received!: () => void;
  const heldResponse = new Promise<void>(resolve => { release = resolve; });
  const requestReceived = new Promise<void>(resolve => { received = resolve; });
  const writes: string[] = [];
  const ticket = { requestId: 'request-late-response', status: 'queued', sceneVersion: 'lab-demo@3', source: 'synthetic', expiresAt: Date.now() / 1000 + 600 };
  await page.route('**/api/v1/sessions', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    received(); await heldResponse;
    await route.fulfill({ status: 202, json: ticket });
  });
  await page.route('**/api/v1/session-requests/request-late-response', async route => {
    expect(route.request().method()).toBe('DELETE');
    writes.push(new URL(route.request().url()).pathname);
    await route.fulfill({ json: { ...ticket, status: 'started', sessionId: 'session-owned-late-ticket' } });
  });
  await page.route('**/api/v1/sessions/*', async route => {
    if (route.request().method() !== 'DELETE') return route.continue();
    writes.push(new URL(route.request().url()).pathname);
    await route.fulfill({ status: 204 });
  });
  try {
    await page.goto('/scenes/lab-demo/3');
    await page.getByRole('button', { name: '启动会话' }).click();
    await requestReceived;
    await page.getByRole('link', { name: '← 场景库', exact: true }).click();
    await expect(page).toHaveURL(/\/scenes$/);
    release();
    await expect.poll(() => writes).toEqual([
      '/api/v1/session-requests/request-late-response',
      '/api/v1/sessions/session-owned-late-ticket',
    ]);
  } finally { release(); }
});
