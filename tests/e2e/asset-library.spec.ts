import { expect, test, type Locator } from '@playwright/test';
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { promisify } from 'node:util';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
async function importPackage(directory: string, resource: 'asset' | 'scene') {
  const executable = resolve(process.platform === 'win32' ? '../../.venv/Scripts/lw.exe' : '../../.venv/bin/lw');
  const { stdout } = await promisify(execFile)(executable, [resource, 'import', directory, '--json'], {
    env: { ...process.env, LW_URL: api, LW_SESSION: '' }, encoding: 'utf8', timeout: 30_000,
  });
  return JSON.parse(stdout);
}
async function gapPackage(directory: string) {
  await cp(resolve('../../tests/fixtures/asset-library/gap-cylinder'), directory, { recursive: true });
  const files: Record<string, { sha256: string; bytes: number }> = {};
  for (const name of ['asset.usda', 'LICENSE.txt']) {
    const content = await readFile(resolve(directory, name));
    files[name] = { sha256: createHash('sha256').update(content).digest('hex'), bytes: content.length };
  }
  await writeFile(resolve(directory, 'provenance.json'), JSON.stringify({ source: 'Hand-authored USD browser fixture', license: 'CC0-1.0', files }));
}
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

test('real imported gap package exposes its provenance, license and actual referencing scene', async ({ page, request }, testInfo) => {
  const directory = testInfo.outputPath('gap-package');
  await gapPackage(directory);
  const imported = await importPackage(directory, 'asset');
  const selector = imported.assetVersion as string;
  const revision = selector.split('@')[1];
  const scene = testInfo.outputPath('referencing-scene');
  await mkdir(scene, { recursive: true });
  await writeFile(resolve(scene, 'scene.yaml'), 'name: asset-ui-reference\ntitle: Asset UI reference\nsources: [newton]\nstage: stage.usda\n');
  await writeFile(resolve(scene, 'devices.yaml'), 'scene: asset-ui-reference\nentities: []\n');
  await writeFile(resolve(scene, 'locations.yaml'), 'locations: {}\n');
  await writeFile(resolve(scene, 'stage.usda'), `#usda 1.0\n(defaultPrim = "World"\n upAxis = "Z"\n metersPerUnit = 1)\ndef Xform "World" {\ndef Xform "Lab" {\ndef Xform "Cylinder" (\n prepend references = @library:assets/labware/ui-cylinder/${revision}/asset.usda@</Asset>\n) {}\n}\n}\n`);
  const sceneImport = await importPackage(scene, 'scene');
  const detail = await (await request.get(`${api}/api/v1/library/assets/labware/ui-cylinder/${revision}`)).json();
  expect(detail.referencingScenes.map((scene: { id: string }) => scene.id)).toContain(sceneImport.sceneVersion);
  const incomplete = testInfo.outputPath('incomplete-scene');
  await cp(scene, incomplete, { recursive: true });
  await writeFile(resolve(incomplete, 'scene.yaml'), 'name: asset-ui-incomplete\ntitle: Asset UI incomplete reference\nsources: [newton]\nstage: stage.usda\n');
  await writeFile(resolve(incomplete, 'devices.yaml'), 'scene: asset-ui-incomplete\nentities: []\n');
  const stage = await readFile(resolve(incomplete, 'stage.usda'), 'utf8');
  await writeFile(resolve(incomplete, 'stage.usda'), stage.replace('\n}\n}\n', '\ndef Xform "Missing" (\n prepend references = @library:assets/environment/missing/1/asset.usda@</Asset>\n) {}\n}\n}\n'));
  const partial = await importPackage(incomplete, 'scene');
  const live: string[] = [];
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/v1/sessions')) live.push(request.url()); });
  page.on('websocket', socket => { if (new URL(socket.url()).pathname.startsWith('/api/')) live.push(socket.url()); });
  await page.goto(`/assets?kind=labware&gaps=true&q=${encodeURIComponent(selector)}&view=list`);
  await expect(page.locator('.asset-list-table tbody tr')).toHaveCount(1);
  await page.locator('.asset-list-table a').filter({ hasText: selector }).click();
  await expect(page.getByRole('heading', { name: 'UI Cylinder', exact: true })).toBeVisible();
  await expect(page.getByText('此素材没有视觉文件', { exact: true })).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(page.getByRole('list', { name: '六项完整度' }).locator('li')).toHaveCount(6);
  await expect(page.getByText('此素材没有绑定模板。', { exact: true })).toBeVisible();
  const license = page.getByRole('link', { name: 'LICENSE.txt ↗', exact: true });
  await expect(license).toBeVisible();
  expect((await request.get(`${api}${await license.getAttribute('href')}`)).ok()).toBe(true);
  await expect(page.getByText('Hand-authored USD browser fixture', { exact: true })).toBeVisible();
  await expect(page.locator('.asset-scene-references a').filter({ hasText: sceneImport.sceneVersion })).toHaveAttribute('href', `/scenes/asset-ui-reference/${sceneImport.sceneVersion.split('@')[1]}`);
  await expect(page.locator('.asset-scene-references')).toContainText(partial.sceneVersion);
  await expect(page.locator('.asset-reference-warning')).toContainText('引用检查尚不完整。');
  await page.screenshot({ path: testInfo.outputPath('real-imported-gap-detail.png'), fullPage: true });
  await page.goto('/assets/robot/fr3/1');
  await expect(page.locator('.asset-binding')).toContainText('fr3_hand/tcp');
  const type = page.getByTestId('asset-type-robot-arm/v1');
  await expect(type).toContainText('jointPositions');
  const pick = page.getByTestId('asset-action-robot-arm/v1-pick');
  await pick.locator('summary').first().click();
  await expect(pick).toContainText('holding');
  await expect(pick).toContainText('isNull');
  await expect(page.getByRole('link', { name: '查看完整来源记录 ↗', exact: true })).toBeVisible();
  expect(live).toEqual([]);
  const current = await (await request.get(`${api}/api/v1/library/assets/labware/ui-cylinder/${revision}`)).json();
  await writeFile(testInfo.outputPath('imported-reference-receipt.json'), JSON.stringify({
    assetVersion: selector, contentHash: current.contentHash, completeness: current.completeness,
    referencingScenes: current.referencingScenes, referenceInspection: current.referenceInspection, liveRequests: live,
  }, null, 2));
});

test('an actually imported invalid thumbnail shows a truthful preview error', async ({ page }, testInfo) => {
  const directory = testInfo.outputPath('invalid-thumbnail-package');
  await gapPackage(directory);
  await mkdir(resolve(directory, 'previews'), { recursive: true });
  await writeFile(resolve(directory, 'previews/thumbnail.png'), 'Invalid image bytes retained as a test input');
  const imported = await importPackage(directory, 'asset');
  const selector = imported.assetVersion as string;
  await page.goto(`/assets?kind=labware&q=${encodeURIComponent(selector)}`);
  const card = page.locator('.asset-card').filter({ hasText: selector });
  await expect(card).toContainText('预览加载失败');
  await expect(page.locator('canvas')).toHaveCount(0);
  await card.click();
  await expect(page.getByRole('alert')).toContainText('预览图无法载入。');
  await expect(page.getByRole('button', { name: '重试预览', exact: true })).toBeVisible();
});

test('unavailable WebGL keeps the asset library and detail readable', async ({ page }) => {
  await page.addInitScript(() => {
    const nativeContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, context: string, options?: unknown) {
      return context.startsWith('webgl') ? null : Reflect.apply(nativeContext, this, [context, options]);
    } as typeof nativeContext;
  });
  await page.goto('/assets?kind=robot');
  const card = page.locator('.asset-card').filter({ hasText: 'robot/fr3@1' });
  await expect(card).toContainText('预览加载失败');
  await expect(page.locator('canvas')).toHaveCount(0);
  await card.click();
  await expect(page.getByRole('heading', { name: 'Franka Research 3 with Franka Hand', exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('模型无法载入');
  await expect(page.getByRole('list', { name: '六项完整度' }).locator('li')).toHaveCount(6);
  await expect(page.getByRole('button', { name: '重试预览', exact: true })).toBeVisible();
});

test('asset API and model failures show retry paths and recover through real responses', async ({ page }) => {
  let unavailable = true;
  await page.route('**/api/v1/library/assets?*', route => unavailable ? route.fulfill({ status: 503, json: { detail: 'Asset index temporarily unavailable' } }) : route.continue());
  await page.goto('/assets');
  await expect(page.getByRole('alert')).toContainText('Asset index temporarily unavailable');
  unavailable = false;
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.locator('.asset-card').filter({ hasText: 'robot/fr3@1' })).toBeVisible();
  await page.getByRole('searchbox', { name: '搜索素材' }).fill('no-such-asset-314159');
  await expect(page.getByRole('heading', { name: '没有匹配的素材' })).toBeVisible();
  await page.getByRole('button', { name: '清除筛选' }).click();
  await expect(page.getByRole('searchbox', { name: '搜索素材' })).toHaveValue('');
  let brokenModel = true;
  await page.route('**/library/files/**/visual.glb', route => brokenModel ? route.fulfill({ status: 503, body: 'Temporary model outage' }) : route.continue());
  await page.goto('/assets/robot/fr3/1');
  await expect(page.getByLabel('素材静态模型预览')).toHaveAttribute('data-model-state', 'error');
  brokenModel = false;
  await page.getByRole('button', { name: '重试预览', exact: true }).click();
  await expect(page.getByLabel('素材静态模型预览')).toHaveAttribute('data-model-state', 'ready');
  await page.goto('/assets/robot/no-such-asset/999');
  await expect(page.getByRole('alert')).toContainText('Unknown asset version');
  await page.getByRole('link', { name: '返回素材库', exact: true }).click();
  await expect(page.getByRole('heading', { name: '素材库', exact: true })).toBeVisible();
});
