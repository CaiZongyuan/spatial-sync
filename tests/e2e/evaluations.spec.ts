import { expect, test } from '@playwright/test';
import { execFile } from 'node:child_process';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import type {} from '../../apps/web/src/components/Workbench';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('evaluation owns a read-only workspace while explicit scene launches cancel or start afterward', async ({ page, request, context }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let manual: string | undefined;
  let evaluation: string | undefined;
  let keptRequest: string | undefined;
  let startedSession: string | undefined;
  let barrierToken: string | undefined;
  let failed = false;
  const observer = await context.newPage();
  observer.on('pageerror', error => errors.push(error.message));
  const acknowledgements: string[] = [];
  for (const browser of [page, observer]) browser.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/ack')) acknowledgements.push(request.url());
  });
  try {
    const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
    const { stdout } = await promisify(execFile)(python, [resolve('../../tests/fixtures/evaluation_alerts.py')], { encoding: 'buffer' });
    const imported = await request.post(`${api}/api/v1/library/imports`, { headers: { 'Content-Type': 'application/x-tar' }, data: stdout });
    expect(imported.ok(), await imported.text()).toBe(true);
    expect((await imported.json()).validation.launchable).toBe(true);
    const fixture = await request.post(`${api}/e2e/evaluation-alert-suite`);
    expect(fixture.ok(), await fixture.text()).toBe(true);
    const { suite, token, suiteContentHash, sceneVersion, sceneContentHash } = await fixture.json();
    barrierToken = token;
    const barrierUrl = `${api}/e2e/evaluation-alert-barriers/${token}`;
    const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneVersion: 'lab-demo@3', source: 'synthetic' } });
    expect(created.status(), await created.text()).toBe(201);
    manual = (await created.json()).sessionId;
    const submitted = await request.post(`${api}/api/v1/evaluations`, { data: { subject: 'robot_arm.virtual', suite } });
    expect(submitted.status(), await submitted.text()).toBe(202);
    evaluation = (await submitted.json()).evaluationId;
    const bound = await request.post(`${barrierUrl}/bind`, { data: { evaluationId: evaluation } });
    expect(bound.ok(), await bound.text()).toBe(true);
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
    // UI work overlaps the real action; hold only its graded first episode before close.
    await expect.poll(async () => (await (await request.get(barrierUrl)).json()).state).toBe('held');
    const held = await (await request.get(barrierUrl)).json();
    expect(held).toMatchObject({
      suite, suiteContentHash, sceneVersion, sceneContentHash, caseId: 'train-one',
      evaluationId: evaluation, state: 'held',
      witness: { evaluationId: evaluation, sessionId: episodeSession, outcome: {
        caseId: 'train-one', success: true, invocationStatus: 'succeeded',
      } },
    });
    expect(held.witness.outcome.milestones).toEqual({ grasp: expect.any(Boolean), place: expect.any(Boolean) });
    const session = await (await request.get(`${api}/api/v1/sessions/${episodeSession}`)).json();
    expect(session).toMatchObject({ state: 'RUNNING', epoch: held.witness.epoch });
    const [alert] = (await (await request.get(`${api}/api/v1/alerts?sessionId=${episodeSession}&active=true`)).json()).alerts;
    expect(alert).toMatchObject({ sessionId: episodeSession, epoch: held.witness.epoch, readOnly: true, active: true, acknowledgement: null });
    const refused = await request.post(`${api}/api/v1/alerts/${alert.alertId}/ack`, { data: {} });
    expect(refused.status()).toBe(409);
    expect((await refused.json()).detail.code).toBe('evaluation_busy');
    const episode = observer.locator(`[data-alert-id="${alert.alertId}"]`);
    await expect(episode).toContainText('活动 · 待确认');
    await expect(episode).toContainText('评估会话只读');
    await expect(episode.getByRole('button', { name: '确认', exact: true })).toHaveCount(0);
    await expect(episode.getByRole('textbox', { name: '确认备注' })).toHaveCount(0);
    expect(acknowledgements).toEqual([]);
    await info.attach('evaluation-devices-readonly.png', { body: await observer.screenshot(), contentType: 'image/png' });
    await observer.getByRole('button', { name: 'Session device telemetry', exact: true }).click();
    await observer.goto('/');
    const banner = observer.getByTestId('critical-alert-banner');
    await expect(banner).toContainText('Evaluation temperature is positive');
    await expect(banner).toContainText('评估会话只读');
    await expect(banner.getByRole('button', { name: '确认', exact: true })).toHaveCount(0);
    await expect(banner.getByRole('textbox', { name: '确认备注' })).toHaveCount(0);
    expect(new URL((await banner.getByRole('link').getAttribute('href'))!, api).pathname).toBe(`/sessions/${episodeSession}`);
    expect(acknowledgements).toEqual([]);
    await info.attach('evaluation-alert-banner-readonly.png', { body: await observer.screenshot(), contentType: 'image/png' });
    await observer.goto(`/sessions/${episodeSession}`);
    expect(await (await request.get(barrierUrl)).json()).toEqual(held);
    const retained = (await (await request.get(`${api}/api/v1/alerts?sessionId=${episodeSession}&active=true`)).json()).alerts;
    expect(retained.find((value: { alertId: string }) => value.alertId === alert.alertId)).toMatchObject({
      alertId: alert.alertId, sessionId: episodeSession, epoch: held.witness.epoch,
      active: true, readOnly: true, acknowledgement: null, closedContext: null,
    });
    const released = await request.post(`${barrierUrl}/release`);
    expect(released.ok(), await released.text()).toBe(true);
    expect((await released.json()).state).toBe('released');
    await expect.poll(async () => (await (await request.get(`${api}/api/v1/evaluations/${evaluation}`)).json()).status, { timeout: 35_000 }).toBe('completed');
    const completed = await (await request.get(`${api}/api/v1/evaluations/${evaluation}`)).json();
    expect(completed.episodes.map((value: { caseId: string }) => value.caseId)).toEqual(['train-one', 'train-two']);
    expect(completed.result).toMatchObject({ successes: 2, episodes: 2, score: 1 });
    expect(completed.result.key).toMatchObject({ suite, suiteContentHash, scene: sceneVersion, sceneContentHash });
    expect(new Set(completed.result.recordings).size).toBe(2);
    expect(completed.episodes[0].recordingId).toBe(episodeSession);
    expect(completed.episodes[0]).toMatchObject(held.witness.outcome);
    expect(new Set(completed.episodes.map((value: { invocationId: string }) => value.invocationId)).size).toBe(2);
    for (const result of completed.episodes) {
      expect(result).toMatchObject({ success: true, invocationStatus: 'succeeded' });
      expect(result.milestones).toEqual({ grasp: expect.any(Boolean), place: expect.any(Boolean) });
      const metadata = await request.get(`${api}/recordings/${result.recordingId}/recording.json`);
      expect(metadata.ok(), await metadata.text()).toBe(true);
      expect((await metadata.json()).status).toBe('complete');
    }
    for (const stage of ['grasp', 'place']) {
      const passed = completed.episodes.filter((result: { milestones: Record<string, boolean> }) => result.milestones[stage]).length;
      expect(completed.result.phases[stage]).toEqual({ passed, episodes: 2, rate: passed / 2 });
    }
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
  } catch (error) {
    failed = true;
    throw error;
  } finally {
    const cleanupErrors: string[] = [];
    const cleanup = async (name: string, operation: () => Promise<unknown>) => {
      try { await operation(); } catch (error) { cleanupErrors.push(`${name}: ${String(error)}`); }
    };
    if (barrierToken) await cleanup('release barrier', async () => {
      const response = await request.post(`${api}/e2e/evaluation-alert-barriers/${barrierToken}/release`, { timeout: 5_000 });
      expect(response.ok(), await response.text()).toBe(true);
    });
    if (evaluation) await cleanup('cancel evaluation', async () => {
      const response = await request.post(`${api}/api/v1/evaluations/${evaluation}/cancel`, { timeout: 5_000 });
      expect(response.ok(), await response.text()).toBe(true);
    });
    await cleanup('close observer', () => observer.close());
    if (keptRequest) await cleanup('cancel queued launch', async () => {
      const response = await request.delete(`${api}/api/v1/session-requests/${keptRequest}`, { timeout: 5_000 });
      expect(response.ok(), await response.text()).toBe(true);
      startedSession ??= (await response.json()).sessionId;
    });
    for (const sessionId of [manual, startedSession].filter(Boolean)) await cleanup(`close ${sessionId}`, async () => {
      expect((await request.delete(`${api}/api/v1/sessions/${sessionId}`, { timeout: 5_000 })).status()).toBe(204);
    });
    if (cleanupErrors.length) {
      await cleanup('attach cleanup errors', () => info.attach('cleanup-errors', { body: cleanupErrors.join('\n'), contentType: 'text/plain' }));
      if (!failed) throw new Error(cleanupErrors.join('\n'));
    }
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
