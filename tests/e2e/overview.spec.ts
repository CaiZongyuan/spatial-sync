import { expect, test } from '@playwright/test';

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

test('the real overview connects an isometric scene, readouts, device actions, activity and theme', async ({ page, request }, testInfo) => {
  const response = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic' } });
  expect(response.ok()).toBe(true);
  const { sessionId } = await response.json();
  let scenes = 0, sockets = 0;
  page.on('request', request => { if (request.url().endsWith(`/sessions/${sessionId}/scene`)) scenes++; });
  page.on('websocket', socket => { if (socket.url().includes(`/sessions/${sessionId}/ws`)) sockets++; });
  try {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
    const label = page.getByTestId(`device-label-${centrifuge}`);
    await expect(label).toBeVisible();
    const drawer = page.getByRole('complementary', { name: '设备抽屉' });
    await expect(drawer).toHaveAttribute('data-read-only', 'false');
    await expect(page.locator('.overview-page-heading')).toContainText(sessionId);
    const initialSceneRequests = scenes;
    const initialSockets = sockets;
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
