import { expect, test } from '@playwright/test';
import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const centrifuge = '/World/Lab/Centrifuge';

test('a paused live viewport draws on changes and wakes for Step, theme and projection', async ({ page, request }, testInfo) => {
  const response = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic' } });
  const { sessionId } = await response.json();
  try {
    await page.goto(`/sessions/${sessionId}`);
    await expect(page.getByTestId('connection-status')).toHaveText('connected');
    await expect.poll(() => page.evaluate(() => window.__spatialDebug?.getSample()?.seq ?? -1)).toBeGreaterThanOrEqual(0);
    await expect(page.locator('canvas')).toBeVisible();
    await page.getByRole('button', { name: 'Pause simulation', exact: true }).click();
    await expect(page.getByTestId('session-state')).toHaveText('PAUSED');
    const receipt = await page.evaluate(async () => {
      const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      await frame(); await frame();
      window.__spatialDebug!.startProfile();
      const start = performance.now();
      await new Promise(resolve => setTimeout(resolve, 600));
      return { elapsedMs: performance.now() - start, samples: window.__spatialDebug!.stopProfile().samples };
    });
    expect(receipt.samples.length / (receipt.elapsedMs / 1000)).toBeLessThan(12);
    const before = await page.evaluate(() => window.__spatialDebug!.getSample()!.seq);
    await page.getByRole('button', { name: 'Step simulation', exact: true }).click();
    await expect.poll(() => page.evaluate(() => window.__spatialDebug!.getSample()!.seq)).toBeGreaterThan(before);
    await expect.poll(() => page.evaluate(() => {
      const key = '/World/Lab/Robot/Link1';
      return JSON.stringify(window.__spatialDebug!.getRenderedPose(key)) === JSON.stringify(window.__spatialDebug!.getAuthoritativePose(key));
    })).toBe(true);
    await page.getByLabel('Color theme').selectOption('light');
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug!.appearance().background)).toBe('f4f3ef');
    await page.getByRole('button', { name: 'Isometric camera', exact: true }).click();
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug!.camera().projection)).toBe('OrthographicCamera');
    await testInfo.attach('paused-render-receipt', { body: Buffer.from(JSON.stringify(receipt)), contentType: 'application/json' });
  } finally { await fetch(`${api}/api/v1/sessions/${sessionId}`, { method: 'DELETE' }); }
});

test('the real overview connects an isometric scene, readouts, device actions and activity', async ({ page, request }) => {
  const response = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic' } });
  expect(response.ok()).toBe(true);
  const { sessionId, sceneVersion } = await response.json();
  try {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
    const label = page.getByTestId(`device-label-${centrifuge}`);
    await expect(label).toBeVisible();
    const drawer = page.getByRole('complementary', { name: '设备抽屉' });
    await expect(drawer).toHaveAttribute('data-read-only', 'false');
    await expect(page.locator('.overview-page-heading')).toContainText(sessionId);
    await expect(page.locator('.overview-page-heading')).toContainText(sceneVersion);
    await expect(page.getByRole('complementary', { name: '检查器' })).toContainText(sceneVersion);
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.camera().projection)).toBe('OrthographicCamera');
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.drawnEntities().length ?? 0)).toBeGreaterThan(20);
    await expect(page.locator('canvas')).toHaveCount(1);
    const boxes = await page.locator('.device-annotation').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()));
    expect(boxes.length).toBeGreaterThanOrEqual(2);
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++)
      expect(boxes[i].right <= boxes[j].left || boxes[j].right <= boxes[i].left || boxes[i].bottom <= boxes[j].top || boxes[j].bottom <= boxes[i].top).toBe(true);
    await page.getByRole('button', { name: '读数', exact: true }).click();
    await expect(page.locator('.device-annotation span')).toHaveCount(0);
    await page.getByRole('button', { name: '读数', exact: true }).click();
    await expect(label.locator('span')).toContainText('phase');
    await label.click();
    await expect(drawer).toContainText('centrifuge/v1');
    await expect(drawer).toContainText('centrifuge.slot1');
    await page.getByTestId('action-closeLid').locator('summary').click();
    await expect(page.getByRole('button', { name: 'Run closeLid', exact: true })).toBeEnabled({ timeout: 4000 });
    await page.getByRole('button', { name: 'Run closeLid', exact: true }).click();
    await expect(label.locator('span')).toContainText('lidOpen false');
    await expect(page.locator('.activity-timeline')).toContainText('closeLid');
    await label.click();
    await expect(page.getByRole('heading', { name: '点击地图上的设备', exact: true })).toBeVisible();
    await label.click();
    await page.getByRole('button', { name: '关闭设备抽屉', exact: true }).click();
    await expect(label).toHaveAttribute('aria-pressed', 'false');
  } finally {
    await fetch(`${api}/api/v1/sessions/${sessionId}`, { method: 'DELETE' });
  }
});

test('the real overview preserves its connection, selected device and preferences across theme and layout changes', async ({ page, request }, testInfo) => {
  const response = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic' } });
  expect(response.ok()).toBe(true);
  const { sessionId } = await response.json();
  let scenes = 0, sockets = 0;
  page.on('request', request => { if (request.url().endsWith(`/sessions/${sessionId}/scene`)) scenes++; });
  page.on('websocket', socket => { if (socket.url().includes(`/sessions/${sessionId}/ws`)) sockets++; });
  try {
    await page.goto('/');
    const label = page.getByTestId(`device-label-${centrifuge}`);
    await expect(label).toBeVisible();
    await expect(page.getByRole('complementary', { name: '设备抽屉' })).toHaveAttribute('data-connection', 'connected');
    const initialSceneRequests = scenes;
    const initialSockets = sockets;
    await page.getByLabel('Color theme').selectOption('dark');
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.appearance().background)).toBe('121211');
    const dark = await page.evaluate(() => window.__spatialRenderDebug?.appearance());
    await page.screenshot({ path: testInfo.outputPath('overview-dark-1440.png'), fullPage: true });
    await page.getByLabel('Color theme').selectOption('light');
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.appearance().background)).toBe('f4f3ef');
    const light = await page.evaluate(() => window.__spatialRenderDebug?.appearance());
    expect(light?.materialColors).not.toEqual(dark?.materialColors);
    expect(scenes).toBe(initialSceneRequests); expect(sockets).toBe(initialSockets);
    await label.click();
    await page.screenshot({ path: testInfo.outputPath('overview-selected-light-1440.png'), fullPage: true });
    await page.setViewportSize({ width: 400, height: 900 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
    await page.screenshot({ path: testInfo.outputPath('overview-selected-light-400.png'), fullPage: true });
    await page.getByLabel('Color theme').selectOption('dark');
    await page.screenshot({ path: testInfo.outputPath('overview-selected-dark-400.png'), fullPage: true });
    await page.getByRole('button', { name: '读数', exact: true }).click();
    await expect(page).toHaveURL(/readouts=false/);
    await page.reload();
    await expect(page.getByRole('complementary', { name: '设备抽屉' })).toContainText('centrifuge/v1');
    await expect(page.getByRole('button', { name: '读数', exact: true })).toHaveAttribute('aria-pressed', 'false');
  } finally {
    await fetch(`${api}/api/v1/sessions/${sessionId}`, { method: 'DELETE' });
  }
});

for (const version of [1, 2]) test(`overview previews the v${version} recorded version with disabled actions and no live transport`, async ({ page }, testInfo) => {
  const fixture = resolve(`../../tests/fixtures/recordings/v${version}`);
  const metadata = JSON.parse(readFileSync(join(fixture, 'recording.json'), 'utf8'));
  const live = { ...JSON.parse(readFileSync(resolve('../../tests/fixtures/recordings/v2/recording.json'), 'utf8')),
    sessionId: 'live-preview-candidate', kind: 'live', sceneId: null, sceneVersion: null, sceneContentHash: null };
  const liveRequests: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/sessions/')) liveRequests.push(request.url());
  });
  page.on('websocket', socket => {
    if (new URL(socket.url()).pathname.startsWith('/api/')) liveRequests.push(socket.url());
  });
  await page.route('**/api/v1/recordings', route => route.fulfill({ json: [live, metadata] }));
  await page.route(`**/recordings/${metadata.sessionId}/**`, async route => {
    const relative = new URL(route.request().url()).pathname.split(`/recordings/${metadata.sessionId}/`)[1];
    if (existsSync(join(fixture, relative))) await route.fulfill({ path: join(fixture, relative) });
    else await route.fulfill({ status: 404 });
  });
  await page.goto('/');
  await expect(page.locator('.overview-page-heading')).toContainText(`lab-demo@${version}`);
  await expect(page.locator('.overview-page-heading')).toContainText('最近录制 · 只读预览');
  await expect(page.getByRole('complementary', { name: '检查器' })).toContainText(`lab-demo@${version}`);
  await expect(page.locator('canvas')).toBeVisible();
  const drawer = page.getByRole('complementary', { name: '设备抽屉' });
  await expect(drawer).toHaveAttribute('data-read-only', 'true');
  await page.getByTestId(`legend-${centrifuge}`).click();
  await page.getByTestId('action-closeLid').locator('summary').click();
  await expect(drawer.getByRole('button', { name: 'Run closeLid', exact: true })).toBeDisabled();
  expect(liveRequests).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath(`overview-recording-v${version}.png`), fullPage: true });
});

test('overview does not admit a stopped session from the platform cache into a recording preview', async ({ page, request }) => {
  const fixture = resolve('../../tests/fixtures/recordings/v1');
  const metadata = JSON.parse(readFileSync(join(fixture, 'recording.json'), 'utf8'));
  let sessionId: string | undefined;
  let stopped: Promise<void> | undefined;
  const prepareStoppedSession = async () => {
    const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic' } });
    expect(created.ok()).toBe(true);
    sessionId = (await created.json()).sessionId;
    await expect.poll(async () => {
      const platform = await (await request.get(`${api}/api/v1/platform`)).json();
      return platform.sessions.find((session: { sessionId: string }) => session.sessionId === sessionId)?.state;
    }, { intervals: [25] }).toBe('RUNNING');
    expect((await request.delete(`${api}/api/v1/sessions/${sessionId}`)).ok()).toBe(true);
    const health = await (await request.get(`${api}/api/v1/health`)).json();
    expect(health.sessions.find((session: { sessionId: string }) => session.sessionId === sessionId)?.state).toBe('STOPPED');
    expect((await (await request.get(`${api}/api/v1/sessions/${sessionId}`)).json()).state).toBe('STOPPED');
  };
  // Load the browser first, then create/cache/stop at its first platform read.
  // Every platform response still comes from the real API and its background cache.
  await page.route('**/api/v1/platform', async route => {
    stopped ??= prepareStoppedSession();
    await stopped;
    await route.continue();
  });
  const platformStates: string[] = [];
  page.on('response', async response => {
    if (new URL(response.url()).pathname === '/api/v1/platform' && response.ok()) {
      const platform = await response.json().catch(() => undefined);
      const state = platform?.sessions.find((session: { sessionId: string }) => session.sessionId === sessionId)?.state;
      if (state) platformStates.push(state);
    }
  });
  const liveRequests: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/sessions/')) liveRequests.push(request.url());
  });
  page.on('websocket', socket => {
    if (new URL(socket.url()).pathname.startsWith('/api/')) liveRequests.push(socket.url());
  });
  await page.route('**/api/v1/recordings', route => route.fulfill({ json: [metadata] }));
  await page.route(`**/recordings/${metadata.sessionId}/**`, async route => {
    const relative = new URL(route.request().url()).pathname.split(`/recordings/${metadata.sessionId}/`)[1];
    if (existsSync(join(fixture, relative))) await route.fulfill({ path: join(fixture, relative) });
    else await route.fulfill({ status: 404 });
  });
  try {
    await page.goto('/');
    await expect(page.locator('.overview-page-heading')).toContainText('最近录制 · 只读预览');
    const drawer = page.getByRole('complementary', { name: '设备抽屉' });
    await expect(drawer).toHaveAttribute('data-read-only', 'true');
    await page.getByTestId(`legend-${centrifuge}`).click();
    await page.getByTestId('action-closeLid').locator('summary').click();
    await expect(drawer.getByRole('button', { name: 'Run closeLid', exact: true })).toBeDisabled();
    expect(platformStates).toContain('RUNNING');
    expect(liveRequests).toEqual([]);
  } finally {
    if (sessionId) await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});

test('overview waits for authoritative session status and recovers after a failed confirmation', async ({ page, request }) => {
  const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic' } });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  let allowConfirmation = false;
  await page.route('**/api/v1/health', route => allowConfirmation ? route.continue() : route.fulfill({
    status: 503, json: { detail: 'Session status temporarily unavailable' },
  }));
  const liveRequests: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/sessions/')) liveRequests.push(request.url());
  });
  page.on('websocket', socket => {
    if (new URL(socket.url()).pathname.startsWith('/api/')) liveRequests.push(socket.url());
  });
  try {
    await page.goto('/');
    await expect(page.getByRole('alert')).toContainText('Session status temporarily unavailable');
    expect(liveRequests).toEqual([]);
    allowConfirmation = true;
    const drawer = page.getByRole('complementary', { name: '设备抽屉' });
    await expect(drawer).toHaveAttribute('data-read-only', 'false');
    await expect(drawer).toHaveAttribute('data-connection', 'connected');
    await expect(page.getByRole('alert')).toHaveCount(0);
    await page.getByTestId(`legend-${centrifuge}`).click();
    await page.getByTestId('action-closeLid').locator('summary').click();
    await expect(drawer.getByRole('button', { name: 'Run closeLid', exact: true })).toBeEnabled();
  } finally {
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});

test('overview rejects malformed v2 identity before opening a replay transport', async ({ page }) => {
  const metadata = JSON.parse(readFileSync(resolve('../../tests/fixtures/recordings/v2/recording.json'), 'utf8'));
  metadata.sceneContentHash = null;
  const replayRequests: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/recordings/')) replayRequests.push(request.url());
  });
  await page.route('**/api/v1/recordings', route => route.fulfill({ json: [metadata] }));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('requires a scene content hash');
  await expect(page.getByRole('heading', { name: '开始一次实验', exact: true })).toBeVisible();
  expect(replayRequests).toEqual([]);
});

test('session projection is shareable and preserves a single real scene connection', async ({ page, request }) => {
  const response = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic' } });
  const { sessionId } = await response.json();
  try {
    await page.goto(`/sessions/${sessionId}?camera=isometric`);
    await expect(page.getByTestId('connection-status')).toHaveText('connected');
    await expect(page.getByRole('button', { name: 'Isometric camera', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.camera().projection)).toBe('OrthographicCamera');
    await page.getByRole('button', { name: 'Perspective camera', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/sessions/${sessionId}$`));
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.camera().projection)).toBe('PerspectiveCamera');
    await page.getByRole('button', { name: 'Isometric camera', exact: true }).click();
    await page.reload();
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.camera().projection)).toBe('OrthographicCamera');
    await expect(page.getByTestId('connection-status')).toHaveText('connected');
  } finally {
    await fetch(`${api}/api/v1/sessions/${sessionId}`, { method: 'DELETE' });
  }
});

test('overview annotations keep working with packed instanced geometry', async ({ page, request }) => {
  const response = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic' } });
  const { sessionId } = await response.json();
  try {
    await page.goto('/?render=instanced');
    await expect(page.locator('.overview-page-heading')).toContainText(sessionId);
    await expect(page.getByRole('complementary', { name: '设备抽屉' })).toHaveAttribute('data-read-only', 'false');
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.frameStats().mode)).toBe('instanced');
    await expect(page.getByTestId(`device-label-${centrifuge}`)).toBeVisible();
    await page.locator('.device-legend summary').click();
    const tube = '/World/Lab/Tubes/Tube01';
    await page.getByTestId(`legend-${tube}`).click();
    await expect(page.getByTestId(`device-label-${tube}`)).toBeVisible();
    await expect(page.getByRole('complementary', { name: '设备抽屉' })).toContainText('tube/v1');
  } finally {
    await fetch(`${api}/api/v1/sessions/${sessionId}`, { method: 'DELETE' });
  }
});
