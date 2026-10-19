import { expect, test, type Locator } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
async function opaqueBounds(image: Locator) {
  return image.evaluate(async element => {
    const image = element as HTMLImageElement;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d')!;
    context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let left = canvas.width, top = canvas.height, right = -1, bottom = -1;
    for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
      if (pixels[(y * canvas.width + x) * 4 + 3] < 240) continue;
      left = Math.min(left, x); top = Math.min(top, y); right = Math.max(right, x); bottom = Math.max(bottom, y);
    }
    return { left, top, right, bottom, width: canvas.width, height: canvas.height };
  });
}
test('real asset API, URL filters, shared thumbnails and FR3 completeness stay static', async ({ page, request }, testInfo) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  const assets = (await (await request.get(`${api}/api/v1/library/assets?kind=robot&q=Franka&gaps=true`)).json()).assets;
  const asset = assets.find((value: { id: string }) => value.id === 'robot/fr3@1');
  expect(asset).toBeTruthy();
  const live: string[] = [], errors: string[] = [];
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/v1/sessions')) live.push(request.url()); });
  page.on('websocket', socket => { if (new URL(socket.url()).pathname.startsWith('/api/')) live.push(socket.url()); });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/assets');
  await page.getByLabel('Color theme').selectOption('dark');
  await expect(page.getByRole('heading', { name: '素材库', exact: true })).toBeVisible();
  const image = page.getByRole('img', { name: 'robot/fr3@1 的素材模型' });
  await expect(image).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);
  const frameBounds = await opaqueBounds(image);
  expect(frameBounds.right).toBeGreaterThan(frameBounds.left);
  expect(frameBounds.bottom).toBeGreaterThan(frameBounds.top);
  expect(frameBounds.left).toBeGreaterThan(0); expect(frameBounds.top).toBeGreaterThan(0);
  expect(frameBounds.right).toBeLessThan(frameBounds.width - 1); expect(frameBounds.bottom).toBeLessThan(frameBounds.height - 1);
  await page.screenshot({ path: testInfo.outputPath('asset-card-dark-1600.png'), fullPage: false });
  const darkImage = await image.getAttribute('src');
  await page.getByLabel('Color theme').selectOption('light');
  await expect.poll(async () => (await image.getAttribute('src')) !== darkImage).toBe(true);
  await expect(page.locator('canvas')).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('asset-card-light-1600.png'), fullPage: false });
  const lightImage = await image.getAttribute('src');
  await page.reload();
  await expect(image).toBeVisible();
  expect((await image.getAttribute('src')) === lightImage).toBe(true);
  await page.getByLabel('素材种类', { exact: true }).selectOption('device');
  await expect(page.getByRole('heading', { name: '没有匹配的素材' })).toBeVisible();
  await expect(page).toHaveURL(/kind=device/);
  await page.getByLabel('素材种类', { exact: true }).selectOption('robot');
  await page.getByRole('checkbox', { name: '只看缺口' }).check();
  await page.getByRole('searchbox', { name: '搜索素材' }).fill('Franka');
  await page.getByRole('button', { name: '列表', exact: false }).click();
  await expect(page.locator('.asset-list-table tbody tr')).toHaveCount(1);
  const query = new URL(page.url()).searchParams;
  expect(Object.fromEntries(query)).toMatchObject({ kind: 'robot', gaps: 'true', q: 'Franka', view: 'list' });
  await page.reload();
  await expect(page.getByRole('searchbox', { name: '搜索素材' })).toHaveValue('Franka');
  await expect(page.getByRole('checkbox', { name: '只看缺口' })).toBeChecked();
  await expect(page.locator('.asset-list-table tbody tr')).toContainText(asset.implementedTypes[0]);
  await expect(image).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('asset-list-light-1600.png'), fullPage: false });
  await page.getByLabel('Color theme').selectOption('dark');
  await expect.poll(() => image.getAttribute('src')).not.toBeNull();
  await page.screenshot({ path: testInfo.outputPath('asset-list-dark-1600.png'), fullPage: false });
  await page.getByRole('link', { name: 'robot/fr3@1 Franka Research 3 with Franka Hand', exact: true }).click();
  await expect(page).toHaveURL(/\/assets\/robot\/fr3\/1$/);
  await expect(page.getByRole('heading', { name: asset.title, exact: true })).toBeVisible();
  const model = page.getByLabel('素材静态模型预览');
  await expect(model).toHaveAttribute('data-model-state', 'ready');
  expect(Number(await model.getAttribute('data-mesh-count'))).toBeGreaterThan(0);
  await expect(page.getByRole('list', { name: '六项完整度' }).locator('li')).toHaveCount(6);
  const statuses = await page.locator('.asset-completeness li').evaluateAll(items => items.map(item => item.getAttribute('data-status')));
  expect(statuses).toEqual(asset.completeness.items.map((item: { status: string }) => item.status));
  await page.screenshot({ path: testInfo.outputPath('asset-detail-dark-1600.png'), fullPage: false });
  await page.getByLabel('Color theme').selectOption('light');
  await page.screenshot({ path: testInfo.outputPath('asset-detail-light-1600.png'), fullPage: false });
  await page.setViewportSize({ width: 400, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
  expect(live).toEqual([]);
  expect(errors).toEqual([]);
  const receipt = testInfo.outputPath('asset-public-receipt.json');
  await writeFile(receipt, JSON.stringify({ id: asset.id, contentHash: asset.contentHash, dimensionsM: asset.dimensionsM, implementedTypes: asset.implementedTypes, completeness: asset.completeness, frameBounds, liveRequests: live, errors }, null, 2));
  await testInfo.attach('asset-public-receipt', { path: receipt, contentType: 'application/json' });
});

test('theme changes during an asset capture retain the complete model in one canvas', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.addInitScript(() => {
    const nativeFrame = window.requestAnimationFrame.bind(window);
    const nativeCancel = window.cancelAnimationFrame.bind(window);
    const queued = new Map<number, FrameRequestCallback>();
    let next = 0;
    window.requestAnimationFrame = callback => { queued.set(++next, callback); return next; };
    window.cancelAnimationFrame = id => { queued.delete(id); };
    Reflect.set(window, '__assetFrames', async (release: boolean) => {
      if (release) { window.requestAnimationFrame = nativeFrame; window.cancelAnimationFrame = nativeCancel; }
      const callbacks = [...queued.values()]; queued.clear();
      await new Promise<void>(resolve => nativeFrame(time => { callbacks.forEach(callback => callback(time)); resolve(); }));
    });
    const seen = new WeakSet<HTMLCanvasElement>(); let count = 0;
    Reflect.set(window, '__assetCanvasCount', () => count);
    new MutationObserver(() => document.querySelectorAll('canvas').forEach(canvas => {
      if (!seen.has(canvas)) { seen.add(canvas); count++; }
    })).observe(document, { childList: true, subtree: true });
  });
  try {
    await page.goto('/assets');
    await expect(page.locator('.asset-model-viewport')).toHaveAttribute('data-model-state', 'ready');
    // Render the loaded model once, then hold the pending capture while changing the real control.
    await page.evaluate(() => Reflect.get(window, '__assetFrames')(false));
    await page.getByLabel('Color theme').selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.evaluate(() => Reflect.get(window, '__assetFrames')(true));
    const image = page.getByRole('img', { name: 'robot/fr3@1 的素材模型' });
    await expect(image).toBeVisible();
    const bounds = await opaqueBounds(image);
    expect(bounds.right).toBeGreaterThan(bounds.left);
    expect(bounds.bottom).toBeGreaterThan(bounds.top);
    expect(bounds.top).toBeGreaterThan(0);
    expect(bounds.bottom).toBeLessThan(bounds.height - 1);
    expect(await page.evaluate(() => Reflect.get(window, '__assetCanvasCount')())).toBe(1);
  } finally { await page.evaluate(() => Reflect.get(window, '__assetFrames')(true)).catch(() => {}); }
});
