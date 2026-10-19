import { expect, test } from '@playwright/test';
import type { SceneManifest } from '../../packages/protocol/types';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('a failed preview does not transfer its error to the next healthy capture', async ({ page, request }) => {
  const { scenes } = await (await request.get(`${api}/api/v1/library/scenes`)).json();
  const failed = scenes.find((scene: { id: string }) => scene.id === 'falling-boxes@1');
  const healthy = scenes.find((scene: { id: string }) => scene.id === 'usd-lab@1');
  for (const document of Object.values(healthy.preview.documents))
    expect((await request.get(`${api}${document}`)).status()).toBe(200);
  await page.route('**/api/v1/library/scenes*', route => route.fulfill({ json: { scenes: [failed, healthy] } }));
  await page.route(`**${failed.preview.documents.manifest}`, route => route.fulfill({ status: 503, body: 'Unavailable capture fixture' }));
  await page.goto('/scenes');
  const first = page.locator('.scene-card').filter({ hasText: 'falling-boxes@1' });
  const second = page.locator('.scene-card').filter({ hasText: 'usd-lab@1' });
  await expect(first).toContainText('预览加载失败');
  await expect(second.getByRole('img', { name: 'usd-lab@1 的真实初始场景' })).toBeVisible();
  await expect(second).not.toContainText('预览加载失败');
  await expect(page.locator('canvas')).toHaveCount(0);
});

test('changing theme while the next preview loads recaptures the same scene and finishes its queue', async ({ page, request }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.addInitScript(() => {
    const seen = new WeakSet<HTMLCanvasElement>();
    let created = 0;
    Object.defineProperty(window, '__previewCanvasCount', { get: () => created });
    new MutationObserver(() => document.querySelectorAll('canvas').forEach(canvas => {
      if (!seen.has(canvas)) { seen.add(canvas); created++; }
    })).observe(document, { childList: true, subtree: true });
  });
  const { scenes } = await (await request.get(`${api}/api/v1/library/scenes`)).json();
  const first = scenes.find((scene: { id: string }) => scene.id === 'falling-boxes@1');
  const second = scenes.find((scene: { id: string }) => scene.id === 'usd-lab@1');
  await page.route('**/api/v1/library/scenes*', route => route.fulfill({ json: {
    scenes: new URL(route.request().url()).searchParams.get('q') ? [first] : [first, second],
  } }));
  let releaseSecond!: () => void;
  const delayed = new Promise<void>(resolve => { releaseSecond = resolve; });
  await page.route(`**${second.preview.documents.manifest}`, async route => {
    const response = await route.fetch();
    await delayed;
    // Changing theme intentionally cancels this old job's held request.
    await route.fulfill({ response }).catch(() => {});
  });
  const secondStarted = page.waitForRequest(request => new URL(request.url()).pathname === second.preview.documents.manifest);
  try {
    await page.goto('/scenes');
    const firstImage = page.getByRole('img', { name: 'falling-boxes@1 的真实初始场景' });
    await expect(firstImage).toBeVisible();
    const light = await firstImage.getAttribute('src');
    await secondStarted;
    await page.getByLabel('Color theme').selectOption('dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    releaseSecond();
    await expect(page.locator('.scene-card .scene-thumbnail img')).toHaveCount(2);
    const dark = await firstImage.getAttribute('src');
    expect(dark).not.toBe(light);
    await expect(page.locator('canvas')).toHaveCount(0);
    expect(await page.evaluate(() => Reflect.get(window, '__previewCanvasCount'))).toBe(1);
    // A fresh single-scene rendering must agree with the rethemed queue result.
    await page.goto('/scenes?q=falling-boxes');
    await expect(firstImage).toBeVisible();
    expect(await firstImage.getAttribute('src')).toBe(dark);
  } finally { releaseSecond(); }
});

test('a library larger than the shared thumbnail cache keeps every completed image', async ({ page, request }) => {
  // Reuse a real small frozen frame; only library identities/revisions are fixtures.
  const { scenes } = await (await request.get(`${api}/api/v1/library/scenes?source=newton`)).json();
  const scene = scenes.find((value: { id: string }) => value.id === 'falling-boxes@1');
  const documents = Object.fromEntries(await Promise.all(Object.entries(scene.preview.documents).map(async ([kind, url]) => {
    const response = await request.get(`${api}${url}`);
    return [kind, kind === 'spatial' ? await response.body() : await response.json()];
  })));
  await page.route('**/api/v1/library/scenes*', route => route.fulfill({ json: { scenes: Array.from({ length: 35 }, (_, index) => ({
    ...scene, id: `thumbnail-fixture@${index + 1}`, sceneId: 'thumbnail-fixture', rev: index + 1,
    preview: { ...scene.preview, documents: Object.fromEntries(Object.keys(documents).map(kind => [kind, `/api/v1/thumbnail-fixture/${index}/${kind}`])) },
  })) } }));
  await page.route('**/api/v1/thumbnail-fixture/**', route => {
    const [index, kind] = new URL(route.request().url()).pathname.split('/').slice(-2);
    return kind === 'spatial' ? route.fulfill({ body: documents.spatial, contentType: 'application/octet-stream' })
      : route.fulfill({ json: { ...documents[kind], sceneRevision: `thumbnail-fixture-${index}` } });
  });
  await page.goto('/scenes');
  await page.waitForFunction(() => document.querySelectorAll('.scene-card .scene-thumbnail img').length === 35);
  await expect(page.locator('.scene-card .scene-thumbnail img')).toHaveCount(35);
  await expect(page.getByRole('img', { name: 'thumbnail-fixture@1 的真实初始场景' })).toBeVisible();
  await expect(page.getByText('预览加载失败', { exact: true })).toHaveCount(0);
  await expect(page.locator('canvas')).toHaveCount(0);
});

test('real scene capture, shared thumbnails, filters, URL preferences and exact launch', async ({ page, request }, testInfo) => {
  const errors: string[] = [], liveRequests: string[] = [], sockets: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().includes('/api/v1/sessions')) liveRequests.push(request.url()); });
  page.on('websocket', socket => { if (new URL(socket.url()).pathname.startsWith('/api/')) sockets.push(socket.url()); });
  const before = await (await request.get(`${api}/api/v1/health`)).json();
  await page.goto('/scenes');
  const laboratory = page.locator('.scene-card').filter({ hasText: 'lab-demo@1' });
  await expect(laboratory).toContainText('30 实体 · 22 绑定');
  await page.waitForFunction(expected => {
    const images = [...document.querySelectorAll<HTMLImageElement>('.scene-card .scene-thumbnail img')];
    return images.length === expected && images.every(image => image.complete && image.naturalWidth > 0);
  }, 5);
  // The shared renderer captures scenes serially; check readiness for each image.
  for (const id of ['falling-boxes@1', 'lab-demo@1', 'lab-demo@2', 'robot-and-tubes@1', 'usd-lab@1'])
    await expect(page.getByRole('img', { name: `${id} 的真实初始场景`, exact: true })).toBeVisible();
  await expect(page.locator('.scene-card .scene-thumbnail img')).toHaveCount(5);
  await expect(page.locator('canvas')).toHaveCount(0);
  await page.getByRole('button', { name: '☷ 列表', exact: true }).click();
  await expect(page).toHaveURL(/view=list/);
  await expect(page.locator('.scene-mini-thumbnail').first()).toHaveCSS('width', '72px');
  await page.reload();
  await expect(page.getByRole('button', { name: '☷ 列表', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByLabel('物理源筛选').selectOption('newton');
  await expect(page.locator('.scene-library-table')).not.toContainText('lab-demo@1');
  await page.getByLabel('物理源筛选').selectOption('synthetic');
  await expect(page.locator('.scene-library-table tbody tr')).toHaveCount(2);
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

test('bundled FR3 preview loads its real GLBs and initial frame without a live session', async ({ page, request }, testInfo) => {
  const live: string[] = [], failures: string[] = [];
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/v1/sessions')) live.push(request.url()); });
  page.on('websocket', socket => { if (new URL(socket.url()).pathname.startsWith('/api/')) live.push(socket.url()); });
  page.on('pageerror', error => failures.push(error.message));
  const detail = await (await request.get(`${api}/api/v1/library/scenes/robot-and-tubes/1`)).json();
  expect(detail.preview.status).toBe('available');
  const manifest = await (await request.get(`${api}${detail.preview.documents.manifest}`)).json() as SceneManifest;
  const bodies = manifest.nodes.filter(node => node.kind === 'body');
  const meshes = manifest.nodes.flatMap(node => node.kind !== 'group' && node.visuals.some(visual => visual.kind !== 'primitive')
    ? [{ key: node.entityKey, count: node.visuals.length }] : []);
  const visible = manifest.nodes.filter(node => node.kind !== 'group'
    && (node.visuals.length || node.fallbackVisuals?.length || node.colliders.length));
  expect(meshes.length).toBeGreaterThan(10);
  await page.goto('/scenes/robot-and-tubes/1');
  await expect(page.locator('.scene-detail-stack').first()).toContainText('72 实体 · 22 绑定');
  await expect(page.locator('canvas')).toHaveCount(1);
  await expect.poll(() => page.evaluate(expected => expected.every(node => {
    const visual = window.__spatialRenderDebug?.inspectVisuals(node.key);
    return visual?.source === 'visuals' && visual.visualCount === node.count
      && visual.meshCount >= node.count && visual.normalsComplete;
  }), meshes)).toBe(true);
  await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.frameStats().appliedBodies)).toBe(bodies.length);
  await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.drawnEntities().length)).toBe(visible.length);
  const drawn = await page.evaluate(() => window.__spatialRenderDebug!.drawnEntities());
  expect(drawn).not.toContain('/World/Lab/RobotA/fr3/base');
  await expect(page.locator('.scene-entity-table')).toContainText('无渲染几何');
  await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.frameStats().potentialTriangles ?? 0)).toBeGreaterThan(20_000);
  await expect(page.locator('.scene-capture-note')).toContainText('仿真时间 0.000 s');
  await expect(page.getByRole('button', { name: /^Run / })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('bundled-fr3-real-static.png'), fullPage: true });
  expect(live).toEqual([]); expect(failures).toEqual([]);
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
