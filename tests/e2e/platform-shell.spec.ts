import { expect, test } from '@playwright/test';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('platform navigation, persistent preferences, system theme and local assets', async ({ page, baseURL }, testInfo) => {
  const external: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).origin !== new URL(baseURL!).origin) external.push(request.url());
  });
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/');
  const navigation = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(navigation.getByRole('link')).toHaveCount(8);
  expect(await navigation.getByRole('link').evaluateAll(links => links.map(link => link.getAttribute('aria-label'))))
    .toEqual(['Overview', 'Live', 'Assets', 'Scenes', 'Resources', 'Approvals', 'Sessions', 'Recordings']);
  await expect(navigation.getByRole('link', { name: 'Approvals', exact: true })).toHaveAttribute('href', '/approvals');
  await expect(navigation.getByRole('link', { name: 'Overview', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(243, 242, 238)');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(18, 18, 17)');
  await page.getByLabel('Color theme').selectOption('light');
  await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Expand sidebar', exact: true })).toBeVisible();
  await expect(page.locator('.platform-sidebar')).toHaveCSS('width', '44px');
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(243, 242, 238)');
  await navigation.getByRole('link', { name: 'Sessions', exact: true }).focus();
  await expect(page.getByRole('tooltip', { name: '会话', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Expand sidebar', exact: true }).click();
  await navigation.getByRole('link', { name: 'Resources', exact: true }).click();
  await expect(page).toHaveURL(/\/resources\/gpu$/);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Compute resources', exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('compute-light.png'), fullPage: true });
  await page.getByLabel('Color theme').selectOption('dark');
  await page.screenshot({ path: testInfo.outputPath('compute-dark.png'), fullPage: true });
  await navigation.getByRole('link', { name: 'Sessions', exact: true }).click();
  await page.reload();
  await expect(page.getByLabel('Session ID', { exact: true })).toBeVisible();
  await navigation.getByRole('link', { name: 'Scenes', exact: true }).click();
  await expect(page.getByRole('heading', { name: '场景库', exact: true })).toBeVisible();
  await navigation.getByRole('link', { name: 'Recordings', exact: true }).click();
  await expect(page).toHaveURL(/\/recordings$/);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Recordings', exact: true })).toBeVisible();
  await page.goto('/recordings/');
  await expect(page.getByRole('heading', { name: 'Recordings', exact: true })).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.load('400 14px "IBM Plex Sans"');
    await document.fonts.load('400 14px "IBM Plex Mono"');
  });
  expect(await page.evaluate(() => document.fonts.check('400 14px "IBM Plex Sans"') && document.fonts.check('400 14px "IBM Plex Mono"'))).toBe(true);
  expect(external).toEqual([]);
});

test('400 px pages and live workspace stay within the page width', async ({ page, request }, testInfo) => {
  await page.setViewportSize({ width: 400, height: 900 });
  for (const path of ['/', '/resources/gpu', '/approvals', '/sessions', '/scenes', '/scenes/lab-demo/1', '/recordings']) {
    await page.goto(path);
    await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Collapse sidebar', exact: true })).toBeHidden();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
  }
  await page.screenshot({ path: testInfo.outputPath('recordings-400.png'), fullPage: true });
  const response = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic' } });
  expect(response.ok()).toBe(true);
  const { sessionId } = await response.json();
  try {
    await page.goto(`/sessions/${sessionId}`);
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    await expect(page.locator('canvas')).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
    await page.screenshot({ path: testInfo.outputPath('workspace-400.png'), fullPage: true });
  } finally {
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});

test('scene details select only a source supported by the exact version', async ({ page, request }) => {
  const detail = await (await request.get(`${api}/api/v1/library/scenes/lab-demo/1`)).json();
  await page.route('**/api/v1/library/scenes/robot-only/1*', route => route.fulfill({ json: {
    ...detail, id: 'robot-only@1', sceneId: 'robot-only', sources: ['newton'],
    preview: { status: 'unavailable', source: 'newton', reason: 'Fixture has no capture', entityCount: null, bindingCount: null },
  } }));
  await page.goto('/scenes/robot-only/1');
  await expect(page.getByLabel('启动物理源', { exact: true })).toHaveValue('newton');
  await expect(page.getByLabel('启动物理源', { exact: true }).getByRole('option')).toHaveCount(1);
  await expect(page.getByRole('button', { name: '启动会话' })).toBeEnabled();
});

test('scene library launches the selected exact version and connects to its real session', async ({ page, request }) => {
  await page.goto('/scenes');
  await page.locator('.scene-card').filter({ hasText: 'lab-demo@1' }).click();
  const source = page.getByLabel('启动物理源', { exact: true });
  await expect(source).toHaveValue('synthetic');
  const created = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') &&
    response.request().method() === 'POST');
  await page.getByRole('button', { name: '启动会话' }).click();
  const response = await created;
  expect(response.request().postDataJSON()).toMatchObject({ sceneVersion: 'lab-demo@1', source: 'synthetic' });
  expect(response.ok()).toBe(true);
  const { sessionId } = await response.json();
  try {
    await expect(page).toHaveURL(new RegExp(`/sessions/${sessionId}$`));
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
  } finally {
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});
