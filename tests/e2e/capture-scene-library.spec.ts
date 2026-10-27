import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import type { SceneManifest } from '../../packages/protocol/types';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNoaGj4DwAFhAKAjM1mJgAAAABJRU5ErkJggg==', 'base64');
type SceneListGate = { ready: boolean; delivered: boolean; release: () => void };
type SceneListGateWindow = Window & { __sceneListSuccessGate: SceneListGate };

test('one failed static image leaves the next card readable without loading 3D', async ({ page, request }) => {
  const { scenes } = await (await request.get(`${api}/api/v1/library/scenes?view=summary`)).json();
  const selected = scenes.slice(0, 2).map((scene: object, index: number) => ({ ...scene, thumbnailUrl: `/fixture-static/${index}.png` }));
  await page.route('**/api/v1/library/scenes?*', route => route.fulfill({ json: { scenes: selected } }));
  await page.route('**/fixture-static/*', route => route.request().url().endsWith('0.png')
    ? route.fulfill({ status: 503, body: 'Controlled failed image' }) : route.fulfill({ body: pixel, contentType: 'image/png' }));
  const heavy: string[] = [];
  page.on('request', value => { if (/\.glb|\/preview\//.test(value.url())) heavy.push(value.url()); });
  await page.goto('/scenes');
  await expect(page.locator('.scene-card').first()).toContainText('预览加载失败');
  await expect(page.locator('.scene-card').nth(1).getByRole('img')).toBeVisible();
  await expect(page.locator('canvas')).toHaveCount(0);
  expect(heavy).toEqual([]);
});

test('a late old-theme image cannot replace the current static image', async ({ page, request }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  const { scenes } = await (await request.get(`${api}/api/v1/library/scenes?view=summary`)).json();
  await page.route('**/api/v1/library/scenes?*', route => route.fulfill({ json: { scenes: [scenes[0]] } }));
  let release!: () => void, started!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { started = resolve; });
  await page.route('**/api/v1/library/thumbnails?*', async route => {
    if (new URL(route.request().url()).searchParams.get('theme') === 'light') { started(); await held; }
    await route.fulfill({ body: pixel, contentType: 'image/png' }).catch(() => {});
  });
  try {
    await page.goto('/scenes'); await ready;
    await page.getByLabel('Color theme').selectOption('dark');
    const image = page.locator('.scene-card img');
    await expect(image).toBeVisible();
    await expect(image).toHaveAttribute('src', /theme=dark/);
    release();
    await expect(image).toHaveAttribute('src', /theme=dark/);
    await expect.poll(() => image.evaluate((value: HTMLImageElement) => value.naturalWidth)).toBeGreaterThan(0);
    await expect(page.locator('canvas')).toHaveCount(0);
  } finally { release(); }
});

test('static images load near the viewport and remain available when scrolling back', async ({ page, request }) => {
  const { scenes } = await (await request.get(`${api}/api/v1/library/scenes?view=summary`)).json();
  const fixture = Array.from({ length: 35 }, (_, index) => ({ ...scenes[0],
    id: `static-fixture@${index + 1}`, sceneId: 'static-fixture', rev: index + 1,
    thumbnailUrl: `/fixture-static/${index}.png`,
  }));
  const requested = new Set<string>();
  await page.route('**/api/v1/library/scenes?*', route => route.fulfill({ json: { scenes: fixture } }));
  await page.route('**/fixture-static/*', route => { requested.add(route.request().url()); return route.fulfill({ body: pixel, contentType: 'image/png' }); });
  await page.goto('/scenes');
  const cards = page.locator('.scene-card');
  await expect(cards).toHaveCount(35);
  await expect(cards.first().getByRole('img')).toBeVisible();
  expect(requested.size).toBeLessThan(35);
  for (let index = 0; index < 35; index++) {
    await cards.nth(index).scrollIntoViewIfNeeded();
    await expect(cards.nth(index).getByRole('img')).toBeVisible();
  }
  await cards.first().scrollIntoViewIfNeeded();
  await expect.poll(() => cards.first().getByRole('img').evaluate((value: HTMLImageElement) => value.naturalWidth)).toBeGreaterThan(0);
  await expect(page.locator('canvas')).toHaveCount(0);
});

test('changing filters retires an already successful old response and preserves retry', async ({ page, request }) => {
  const count = async (query: string) => (await (await request.get(`${api}/api/v1/library/scenes?view=summary&${query}`)).json()).scenes.length;
  const [all, synthetic, searched] = [await count(''), await count('source=synthetic'), await count('source=synthetic&q=lab-demo')];
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    const gate: SceneListGate = { ready: false, delivered: false, release: () => {} };
    const held = new Promise<void>(resolve => { gate.release = resolve; });
    (window as SceneListGateWindow).__sceneListSuccessGate = gate;
    window.fetch = async (input, init) => {
      const response = await original(input, init);
      const url = new URL(typeof input === 'object' && 'url' in input ? input.url : String(input), location.href);
      if (url.pathname === '/api/v1/library/scenes' && url.searchParams.get('source') === 'newton') {
        const read = response.json.bind(response);
        response.json = async () => { const value = await read(); gate.ready = true; await held; gate.delivered = true; return value; };
      }
      return response;
    };
  });
  let fail = true;
  await page.route('**/api/v1/library/scenes?*', route => {
    if (new URL(route.request().url()).searchParams.get('q') === 'lab-demo' && fail) {
      fail = false; return route.fulfill({ status: 503, json: { detail: 'Controlled scene retry' } });
    }
    return route.continue();
  });
  try {
    await page.goto('/scenes'); await expect(page.locator('.scene-card')).toHaveCount(all);
    await page.getByLabel('物理源筛选').selectOption('newton');
    await page.waitForFunction(() => (window as SceneListGateWindow).__sceneListSuccessGate.ready);
    await expect(page.locator('.scene-card')).toHaveCount(0);
    await page.getByLabel('物理源筛选').selectOption('synthetic');
    await expect(page.locator('.scene-card')).toHaveCount(synthetic);
    await page.evaluate(() => (window as SceneListGateWindow).__sceneListSuccessGate.release());
    await page.waitForFunction(() => (window as SceneListGateWindow).__sceneListSuccessGate.delivered);
    await expect(page.locator('.scene-card')).toHaveCount(synthetic);
    await page.getByLabel('搜索场景').fill('lab-demo');
    await expect(page.getByRole('alert')).toContainText('Controlled scene retry');
    await page.getByRole('button', { name: '从第一页重试' }).click();
    await expect(page.locator('.scene-card')).toHaveCount(searched);
    await expect(page.locator('canvas')).toHaveCount(0);
  } finally { await page.evaluate(() => (window as SceneListGateWindow).__sceneListSuccessGate?.release()).catch(() => {}); }
});

async function watchStaticScene(page: Page, request: APIRequestContext) {
  const errors: string[] = [], liveRequests: string[] = [], sockets: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().includes('/api/v1/sessions')) liveRequests.push(request.url()); });
  page.on('websocket', socket => { if (new URL(socket.url()).pathname.startsWith('/api/')) sockets.push(socket.url()); });
  const before = await (await request.get(`${api}/api/v1/health`)).json();
  return {
    errors,
    async assertStatic() {
      expect(liveRequests).toEqual([]); expect(sockets).toEqual([]);
      const after = await (await request.get(`${api}/api/v1/health`)).json();
      expect(after.sessionCount).toBe(before.sessionCount);
    },
  };
}

test('real capture can be saved once and browsed as a static image without a live session', async ({ page, request }) => {
  const { errors, assertStatic } = await watchStaticScene(page, request);
  await page.goto('/scenes/lab-demo/1');
  await expect(page.locator('canvas')).toHaveCount(1);
  await page.getByRole('button', { name: '生成列表缩略图' }).click();
  await expect(page.getByText('已保存列表缩略图。', { exact: true })).toBeVisible();
  const heavy: string[] = [];
  page.on('request', value => { if (/\.glb|\/preview\//.test(value.url())) heavy.push(value.url()); });
  await page.goto('/scenes');
  const laboratory = page.locator('.scene-card').filter({ hasText: 'lab-demo@1' });
  await expect(laboratory).toBeVisible();
  const image = laboratory.getByRole('img', { name: 'lab-demo@1 的静态预览', exact: true });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((value: HTMLImageElement) => value.naturalWidth)).toBeGreaterThan(20);
  await expect(page.locator('canvas')).toHaveCount(0);
  expect(heavy).toEqual([]);
  await assertStatic(); expect(errors).toEqual([]);
});

test('scene library filters and URL preferences navigate to the exact version', async ({ page, request }) => {
  const { errors, assertStatic } = await watchStaticScene(page, request);
  await page.goto('/scenes');
  await page.getByRole('button', { name: '列表', exact: true }).click();
  await expect(page).toHaveURL(/view=list/);
  await expect(page.locator('.scene-mini-thumbnail').first()).toHaveCSS('width', '72px');
  await page.reload();
  await expect(page.getByRole('button', { name: '列表', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByLabel('物理源筛选').selectOption('newton');
  await expect(page.locator('.scene-library-table')).not.toContainText('lab-demo@1');
  await page.getByLabel('物理源筛选').selectOption('synthetic');
  await expect(page.locator('.scene-library-table tbody tr')).toHaveCount(3);
  await page.getByLabel('搜索场景').fill('not-present');
  await expect(page.getByRole('heading', { name: '没有匹配的场景' })).toBeVisible();
  await page.getByRole('button', { name: '清除筛选' }).click();
  await page.getByRole('link', { name: 'lab-demo@1 Synthetic Laboratory', exact: true }).click();
  await expect(page).toHaveURL(/\/scenes\/lab-demo\/1/);
  await assertStatic();
  expect(errors).toEqual([]);
});

test('an exact scene detail stays static until its selected version launches', async ({ page, request }, testInfo) => {
  const { errors, assertStatic } = await watchStaticScene(page, request);
  await page.goto('/scenes/lab-demo/1');
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
  await assertStatic();
  await page.screenshot({ path: testInfo.outputPath('scene-detail-real.png'), fullPage: true });
  const creating = page.waitForResponse(response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '启动会话' }).click();
  const response = await creating;
  expect(response.ok(), await response.text()).toBe(true);
  expect(response.request().postDataJSON()).toEqual({ sceneVersion: 'lab-demo@1', source: 'synthetic' });
  const { sessionId } = await response.json();
  try {
    await expect(page).toHaveURL(new RegExp(`/sessions/${sessionId}$`));
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
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
