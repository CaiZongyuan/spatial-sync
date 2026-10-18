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
  await expect(navigation.getByRole('link')).toHaveCount(4);
  await expect(navigation.getByRole('link', { name: 'Overview', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(244, 243, 239)');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(18, 18, 17)');
  await page.getByLabel('Color theme').selectOption('light');
  await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Expand sidebar', exact: true })).toBeVisible();
  await expect(page.locator('.platform-sidebar')).toHaveCSS('width', '44px');
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(244, 243, 239)');
  await navigation.getByRole('link', { name: 'Sessions', exact: true }).focus();
  await expect(page.getByRole('tooltip', { name: 'Sessions', exact: true })).toBeVisible();
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
  await expect(page.getByLabel('Scene', { exact: true })).toBeVisible();
  await navigation.getByRole('link', { name: 'Recordings', exact: true }).click();
  await expect(page).toHaveURL(/\/recordings$/);
  await page.reload();
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
  for (const path of ['/', '/resources/gpu', '/sessions', '/recordings']) {
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
    await expect(page.getByTestId('connection-status')).toHaveText('connected');
    await expect(page.locator('canvas')).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
    await page.screenshot({ path: testInfo.outputPath('workspace-400.png'), fullPage: true });
  } finally {
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});

test('the temporary launch form selects a source supported by the first available scene', async ({ page }) => {
  await page.route('**/api/v1/scenes', route => route.fulfill({ json: {
    scenes: [{ sceneId: 'robot-only', name: 'Robot laboratory', sources: ['newton'] }],
  } }));
  await page.goto('/sessions');
  await expect(page.getByLabel('Scene', { exact: true })).toHaveValue('robot-only');
  await expect(page.getByLabel('Source', { exact: true })).toHaveValue('newton');
  await expect(page.getByRole('button', { name: 'Launch workspace' })).toBeEnabled();
});
