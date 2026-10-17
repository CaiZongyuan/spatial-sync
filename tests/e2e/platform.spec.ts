import { expect, test, type Page } from '@playwright/test';
import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import type { PlatformSnapshot } from '../../apps/web/src/lib/platform';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const summary = (page: Page, name: string) => page.getByRole('article', { name, exact: true }).locator('strong');

function sample(): PlatformSnapshot {
  return {
    sampledAtMs: Date.now(), samplingIntervalSeconds: 2,
    summary: { totalSessions: 3, runningSessions: 1, pausedSessions: 0, connectedSessions: 1, connectedClients: 2, newtonBackends: 1 },
    sessions: [{ sessionId: 'newton-live', sceneId: 'robot-and-tubes', sceneName: 'Robot and tubes', source: 'newton', state: 'RUNNING', connectedClients: 2, simTime: 3, actualRtf: 1,
      backend: { pid: 12345, alive: true, device: 'cuda:0', cpuPercent: 240.5, memoryBytes: 1024 ** 3, metricsReason: null,
        viewer: { status: 'not_started', url: null, clientCount: 0, reason: null } } },
      { sessionId: 'synthetic-old', sceneId: 'lab-demo', sceneName: 'Synthetic lab', source: 'synthetic', state: 'STOPPED', connectedClients: 0, simTime: 4, actualRtf: 0, backend: null },
      { sessionId: 'newton-old', sceneId: 'robot-and-tubes', sceneName: 'Previous robot', source: 'newton', state: 'STOPPED', connectedClients: 0, simTime: 5, actualRtf: null,
        backend: { pid: 12344, alive: false, device: null, cpuPercent: null, memoryBytes: null, metricsReason: 'Process has exited.',
          viewer: { status: 'unavailable', url: null, clientCount: null, reason: 'Session is stopped.' } } }],
    system: { cpu: { percent: 0, logicalCores: 16, reason: null }, memory: { totalBytes: 16 * 1024 ** 3, usedBytes: 4 * 1024 ** 3, availableBytes: 12 * 1024 ** 3, percent: 25, reason: null },
      gpus: [{ index: 0, name: 'Example GPU', utilizationPercent: null, memoryUsedBytes: null, memoryTotalBytes: 8 * 1024 ** 3, temperatureC: null }], gpuStatus: 'available', gpuMessage: null },
  };
}

test('platform contract UI preserves unknown metrics, reports stale errors and refreshes counts', async ({ page }) => {
  let value = sample();
  let fail = false;
  await page.route('**/api/v1/platform', route => fail ? route.fulfill({ status: 503, json: { detail: 'Host sampler unavailable' } }) : route.fulfill({ json: value }));
  await page.goto('/');
  await expect(summary(page, 'Running')).toHaveText('1');
  await expect(summary(page, 'Newton backends alive')).toHaveText('1');
  await expect(page.getByRole('article', { name: 'Host CPU', exact: true }).locator('strong')).toHaveText('0.0%');
  await expect(page.getByRole('article', { name: 'GPU utilization', exact: true }).locator('strong')).toHaveText('Not available');
  await expect(page.getByRole('article', { name: 'GPU memory', exact: true }).locator('strong')).toHaveText('Not available');
  await expect(page.getByTestId('platform-session-newton-live')).toContainText('240.5%');
  await expect(page.getByTestId('platform-session-newton-old').getByRole('button', { name: 'Open Newton viewer' })).toBeDisabled();
  await expect(page.getByTestId('platform-session-synthetic-old').getByRole('button', { name: 'Open Newton viewer' })).toHaveCount(0);
  await expect(page.getByLabel('Scene', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Session ID', { exact: true })).toBeVisible();
  fail = true;
  await expect(page.getByRole('alert').filter({ hasText: 'Host sampler unavailable' })).toBeVisible();
  await expect(summary(page, 'Running')).toHaveText('1');
  await expect(page.getByRole('status').filter({ hasText: 'Sample is stale' })).toBeVisible();
  fail = false;
  value = sample(); value.summary.runningSessions = 0; value.summary.pausedSessions = 1; value.summary.connectedClients = 0;
  value.sessions[0].state = 'PAUSED'; value.sessions[0].connectedClients = 0;
  await expect(summary(page, 'Paused')).toHaveText('1');
  await expect(page.getByTestId('platform-session-newton-live')).toContainText('0 connected clients');
  await expect(page.getByRole('status').filter({ hasText: 'Live' })).toBeVisible();
  value.sessions[0].backend!.viewer = { status: 'unavailable', url: null, clientCount: null, reason: 'Only available on the gateway machine.' };
  await expect(page.getByTestId('platform-session-newton-live').getByRole('button', { name: 'Open Newton viewer' })).toBeDisabled();
  await expect(page.getByTestId('platform-session-newton-live')).toContainText('Only available on the gateway machine.');
});

test('viewer contract validates a sampled ready URL, reserves its tab and reports failures', async ({ page, context }) => {
  const value = sample(); let posts = 0; let reject = false;
  // The sampled viewer closed during a reload before the next platform refresh.
  value.sessions[0].backend!.viewer = { status: 'ready', url: 'http://127.0.0.1:1/old-viewer', clientCount: 0, reason: null };
  let release!: () => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/platform', route => route.fulfill({ json: { ...value, sampledAtMs: Date.now() } }));
  await page.route('**/api/v1/sessions/newton-live/viewer', async route => {
    posts++; await waiting;
    await route.fulfill(reject ? { status: 503, json: { detail: { message: 'Native viewer could not start. Try again.' } } } :
      { json: { status: 'ready', url: `${new URL(page.url()).origin}/test-native-viewer`, backendPid: 12345 } });
  });
  await context.route('**/test-native-viewer', route => route.fulfill({ contentType: 'text/html', body: '<p>Viewer contract destination</p>' }));
  await page.goto('/');
  const button = page.getByTestId('platform-session-newton-live').getByRole('button', { name: 'Open Newton viewer' });
  const opened = page.waitForEvent('popup'); await button.click(); const tab = await opened;
  await expect(page.getByRole('button', { name: 'Opening viewer…' })).toBeDisabled();
  await expect.poll(() => posts).toBe(1); expect(tab.url()).toBe('about:blank');
  release();
  await expect(tab).toHaveURL(/test-native-viewer$/);
  await tab.close();
  const reused = page.waitForEvent('popup'); await button.click(); const second = await reused;
  await expect(second).toHaveURL(/test-native-viewer$/); expect(posts).toBe(2); await second.close();
  // A new click must still expose a current server failure despite sampled ready metadata.
  reject = true; await page.reload();
  const failed = page.waitForEvent('popup'); await button.click(); const failedTab = await failed;
  await expect(page.getByRole('alert').filter({ hasText: 'Native viewer could not start' })).toBeVisible();
  await expect.poll(() => failedTab.isClosed()).toBe(true); expect(posts).toBe(3);
});

test('blocked popups do not start a native viewer', async ({ page }) => {
  let posts = 0;
  await page.addInitScript(() => { window.open = () => null; });
  await page.route('**/api/v1/platform', route => route.fulfill({ json: sample() }));
  await page.route('**/api/v1/sessions/*/viewer', route => { posts++; return route.fulfill({ status: 500 }); });
  await page.goto('/');
  await page.getByTestId('platform-session-newton-live').getByRole('button', { name: 'Open Newton viewer' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Allow pop-ups' })).toBeVisible(); expect(posts).toBe(0);
});

test('viewer contract discards a pending URL after its session stops', async ({ page }) => {
  const value = sample();
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/platform', route => route.fulfill({ json: { ...value, sampledAtMs: Date.now() } }));
  await page.route('**/api/v1/sessions/newton-live/viewer', async route => {
    await pending;
    await route.fulfill({ json: { status: 'ready', url: 'http://127.0.0.1:1/', backendPid: 12345 } });
  });
  await page.goto('/');
  const opened = page.waitForEvent('popup');
  await page.getByTestId('platform-session-newton-live').getByRole('button', { name: 'Open Newton viewer' }).click();
  const tab = await opened;
  value.sessions[0].state = 'STOPPED'; value.sessions[0].backend!.alive = false;
  value.summary.runningSessions = 0; value.summary.newtonBackends = 0;
  await expect(summary(page, 'Newton backends alive')).toHaveText('0');
  await expect(page.getByTestId('platform-session-newton-live')).toContainText('STOPPED');
  release();
  await expect(page.getByRole('alert').filter({ hasText: 'The session changed' })).toBeVisible();
  await expect.poll(() => tab.isClosed()).toBe(true);
  await expect(page.getByTestId('platform-session-newton-live').getByRole('button', { name: 'Open Newton viewer' })).toBeDisabled();
});

test('readonly Newton recording never offers a native viewer', async ({ page, baseURL }) => {
  const fixture = resolve('../../tests/fixtures/recordings/v1');
  const manifest = JSON.parse(readFileSync(join(fixture, 'manifest.json'), 'utf8'));
  manifest.source.kind = 'newton';
  await page.route('**/fixture/readonly-native/**', async route => {
    const relative = new URL(route.request().url()).pathname.split('/fixture/readonly-native/')[1];
    if (relative === 'manifest.json') await route.fulfill({ json: manifest });
    else if (existsSync(join(fixture, relative))) await route.fulfill({ path: join(fixture, relative) });
    else await route.fulfill({ status: 404 });
  });
  const src = new URL('/fixture/readonly-native/recording.json', baseURL).href;
  await page.goto(`/replay/readonly-native?src=${encodeURIComponent(src)}`);
  await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Open Newton viewer' })).toHaveCount(0);
});

test('real platform counts follow a session, observer connection, pause and stop', async ({ page, context, request }) => {
  const response = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic' } });
  expect(response.ok()).toBe(true); const { sessionId } = await response.json();
  const observer = await context.newPage();
  try {
    await page.goto('/');
    const row = page.getByTestId(`platform-session-${sessionId}`);
    await expect(row).toContainText('RUNNING'); await expect(row).toContainText('0 connected clients');
    await expect(row.getByRole('link', { name: 'Open workspace' })).toHaveAttribute('href', `/sessions/${sessionId}`);
    await observer.goto(`/sessions/${sessionId}`);
    await expect(observer.getByTestId('connection-status')).toHaveText('connected');
    await expect(row).toContainText('1 connected client');
    await observer.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(row).toContainText('PAUSED');
    await observer.close();
    await expect(row).toContainText('0 connected clients');
    expect((await request.delete(`${api}/api/v1/sessions/${sessionId}`)).ok()).toBe(true);
    await expect(row).toContainText('STOPPED');
    const latest = await (await request.get(`${api}/api/v1/platform`)).json() as PlatformSnapshot;
    await expect(summary(page, 'Running')).toHaveText(String(latest.summary.runningSessions));
    await expect(summary(page, 'Newton backends alive')).toHaveText(String(latest.summary.newtonBackends));
  } finally {
    if (!observer.isClosed()) await observer.close();
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});

test('real native Newton viewer shares its runner and becomes unavailable after stop', async ({ page, context, request }, testInfo) => {
  test.skip(process.env.SPATIAL_PLATFORM_NEWTON_E2E !== '1', 'Requires the real same-runner native viewer integration');
  test.setTimeout(180_000);
  const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'robot-and-tubes', source: 'newton' } });
  expect(created.ok(), await created.text()).toBe(true);
  const { sessionId } = await created.json();
  const openedTabs: Page[] = [];
  try {
    await page.goto('/');
    const row = page.getByTestId(`platform-session-${sessionId}`);
    await expect(row).toContainText('RUNNING', { timeout: 120_000 });
    const before = await (await request.get(`${api}/api/v1/platform`)).json() as PlatformSnapshot;
    const runnerPid = before.sessions.find(session => session.sessionId === sessionId)!.backend!.pid;
    const popup = page.waitForEvent('popup');
    await row.getByRole('button', { name: 'Open Newton viewer' }).click();
    const native = await popup; openedTabs.push(native);
    await expect(native).toHaveURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 30_000 });
    await expect(native.getByText('Simulation control stays in Spatial Sync.', { exact: true })).toBeVisible();
    await expect(native.locator('canvas')).toBeVisible();
    await expect.poll(async () => {
      const current = await (await request.get(`${api}/api/v1/platform`)).json() as PlatformSnapshot;
      return current.sessions.find(session => session.sessionId === sessionId)?.backend?.viewer.clientCount ?? 0;
    }).toBeGreaterThanOrEqual(1);
    const after = await (await request.get(`${api}/api/v1/platform`)).json() as PlatformSnapshot;
    expect(after.sessions.find(session => session.sessionId === sessionId)!.backend!.pid).toBe(runnerPid);
    expect(after.summary.newtonBackends).toBe(before.summary.newtonBackends);
    await native.screenshot({ path: testInfo.outputPath('native-newton-scene.png') });
    const repeat = page.waitForEvent('popup');
    await row.getByRole('button', { name: 'Open Newton viewer' }).click();
    const second = await repeat; openedTabs.push(second);
    await expect(second).toHaveURL(native.url());
    expect((await request.delete(`${api}/api/v1/sessions/${sessionId}`)).ok()).toBe(true);
    await expect(row).toContainText('STOPPED');
    await expect(row.getByRole('button', { name: 'Open Newton viewer' })).toBeDisabled();
    const stopped = await (await request.get(`${api}/api/v1/platform`)).json() as PlatformSnapshot;
    expect(stopped.sessions.find(session => session.sessionId === sessionId)!.backend?.alive ?? false).toBe(false);
    expect(stopped.summary.newtonBackends).toBe(before.summary.newtonBackends - 1);
  } finally {
    for (const tab of openedTabs) if (!tab.isClosed()) await tab.close();
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});
