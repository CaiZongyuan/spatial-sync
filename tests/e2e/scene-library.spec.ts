import { expect, test } from '@playwright/test';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('real scene capture, shared thumbnails, filters, URL preferences and exact launch', async ({ page, request }, testInfo) => {
  const errors: string[] = [], liveRequests: string[] = [], sockets: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().includes('/api/v1/sessions')) liveRequests.push(request.url()); });
  page.on('websocket', socket => { if (new URL(socket.url()).pathname.startsWith('/api/')) sockets.push(socket.url()); });
  const before = await (await request.get(`${api}/api/v1/health`)).json();
  await page.goto('/scenes');
  const laboratory = page.locator('.scene-card').filter({ hasText: 'lab-demo@1' });
  await expect(laboratory).toContainText('30 实体 · 22 绑定');
  await expect(laboratory.getByRole('img', { name: 'lab-demo@1 的真实初始场景' })).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);
  await page.getByRole('button', { name: '☷ 列表', exact: true }).click();
  await expect(page).toHaveURL(/view=list/);
  await expect(page.locator('.scene-mini-thumbnail').first()).toHaveCSS('width', '72px');
  await page.reload();
  await expect(page.getByRole('button', { name: '☷ 列表', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByLabel('物理源筛选').selectOption('newton');
  await expect(page.locator('.scene-library-table')).not.toContainText('lab-demo@1');
  await page.getByLabel('物理源筛选').selectOption('synthetic');
  await expect(page.locator('.scene-library-table tbody tr')).toHaveCount(1);
  await page.getByLabel('搜索场景').fill('not-present');
  await expect(page.getByRole('heading', { name: '没有匹配的场景' })).toBeVisible();
  await page.getByRole('button', { name: '清除筛选' }).click();
  await page.getByRole('link', { name: 'lab-demo@1 Synthetic Laboratory', exact: true }).click();
  await expect(page).toHaveURL(/\/scenes\/lab-demo\/1/);
  await expect(page.locator('canvas')).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.drawnEntities().length ?? 0)).toBeGreaterThan(20);
  await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.camera().projection)).toBe('OrthographicCamera');
  await expect(page.getByRole('button', { name: '初始读数' })).toHaveAttribute('aria-pressed', 'true');
  const label = page.getByTestId('device-label-/World/Lab/Centrifuge');
  await expect(label.locator('span')).toContainText('lidOpen true');
  await page.getByRole('button', { name: '初始读数' }).click();
  await expect(page.locator('.device-annotation span')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '示教位置 · 布景' })).toBeVisible();
  await expect(page.locator('.scene-teaching')).toContainText('rack.A1');
  await expect(page.locator('.scene-validation')).toContainText('Physics checks not run');
  await expect(page.getByRole('button', { name: /^Run / })).toHaveCount(0);
  expect(liveRequests).toEqual([]); expect(sockets).toEqual([]);
  const after = await (await request.get(`${api}/api/v1/health`)).json();
  expect(after.sessionCount).toBe(before.sessionCount);
  await page.screenshot({ path: testInfo.outputPath('scene-detail-real.png'), fullPage: true });
  const creating = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '启动会话' }).click();
  const response = await creating;
  expect(response.ok(), await response.text()).toBe(true);
  expect(response.request().postDataJSON()).toEqual({ sceneVersion: 'lab-demo@1', source: 'synthetic' });
  const { sessionId } = await response.json();
  try {
    await expect(page).toHaveURL(new RegExp(`/sessions/${sessionId}$`));
    await expect(page.getByTestId('connection-status')).toHaveText('connected');
    await expect(page.locator('canvas')).toHaveCount(1);
  } finally { await request.delete(`${api}/api/v1/sessions/${sessionId}`); }
  expect(errors).toEqual([]);
});

test('unlaunchable and missing previews show real reasons and unknown counts', async ({ page, request }) => {
  const detail = await (await request.get(`${api}/api/v1/library/scenes/lab-demo/1`)).json();
  await page.route('**/api/v1/library/scenes/lab-demo/1*', route => route.fulfill({ json: {
    ...detail, launchable: false, launchReason: 'P4: contact instability', entityCount: null, deviceCount: null,
    countsReason: '没有此版本的采集',
    preview: { status: 'unavailable', source: 'synthetic', reason: '没有此版本的采集', entityCount: null, bindingCount: null },
    validation: { ...detail.validation, launchable: false, checks: [{ id: 'P4', tier: 'physics', status: 'fail', message: 'P4: contact instability' }] },
  } }));
  await page.goto('/scenes/lab-demo/1');
  await expect(page.getByRole('button', { name: '启动会话' })).toBeDisabled();
  await expect(page.locator('.scene-launch')).toContainText('P4: contact instability');
  await expect(page.locator('.scene-preview-panel')).toContainText('没有此版本的采集');
  await expect(page.locator('.scene-detail-stack').first()).toContainText('— 实体 · — 绑定');
  await expect(page.locator('canvas')).toHaveCount(0);
});
