import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { writeFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import { cleanupOwnedProcesses, finishCleanup, type OwnedProcess } from './owned-cleanup';

async function freePort() {
  return new Promise<number>((done, reject) => {
    const server = createServer(); server.once('error', reject);
    server.listen(0, '127.0.0.1', () => { const port = (server.address() as { port: number }).port; server.close(error => error ? reject(error) : done(port)); });
  });
}

test('released metadata has compact cards, encoded identities, bounded pages and acquisition intent', async ({ page, request }, info) => {
  test.setTimeout(90_000);
  const [apiPort, webPort] = await Promise.all([freePort(), freePort()]);
  const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const owned = resolve('../../tests/fixtures/owned_processes.py'), execute = promisify(execFile);
  const processes: OwnedProcess[] = [], original: unknown[] = [];
  const env = { ...process.env, SPATIAL_ALLOWED_ORIGINS: web, SPATIAL_API_URL: api, SPATIAL_LIVE_BINDINGS: '',
    LW_LIBRARY: info.outputPath('library'), SPATIAL_STATE_ROOT: info.outputPath('state'),
    SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings') };
  const start = (label: string, executable: string, args: string[], cwd: string) => {
    const child = spawn(executable, args, { cwd, env, windowsHide: true });
    const record: OwnedProcess = { child, label, output: '', exited: new Promise<void>(done => child.once('exit', () => done())),
      identity: execute(python, [owned, 'snapshot', String(child.pid)]).then(async value => {
        await writeFile(info.outputPath(`${label}-creating-identity.json`), value.stdout);
        return { identity: value.stdout };
      }, error => ({ error })) };
    child.stdout?.on('data', chunk => { record.output += chunk; }); child.stderr?.on('data', chunk => { record.output += chunk; });
    child.once('error', error => { record.error = error; }); processes.push(record);
  };
  try {
    start('catalog-metadata-api', python, ['-m', 'uvicorn', 'catalog_metadata_browser_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'));
    start('catalog-metadata-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd());
    await expect.poll(async () => {
      for (const child of processes) if (child.error || child.child.exitCode !== null) throw child.error ?? new Error(child.output);
      try { return (await request.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await request.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 30_000 }).toBe(true);
  const remote: string[] = [];
  page.on('request', value => { if (/huggingface\.co|cdn-lfs/.test(value.url())) remote.push(value.url()); });
  let releasePage!: () => void;
  let failSearch = false;
  let pageGate: Promise<void> | undefined = new Promise(resolve => { releasePage = resolve; });
  await page.route('**/api/v1/library/search?**', async route => {
    const gate = pageGate;
    if (gate) await gate;
    if (failSearch) { failSearch = false; await route.abort('failed'); return; }
    await route.continue();
  });
  await page.goto(`${web}/assets?scope=online&provider=scenario`);
  const cards = page.locator('.catalog-card.is-online');
  await expect(page.getByRole('status', { name: '素材加载状态' })).toContainText('第 1 页');
  await expect(page.locator('.catalog-skeleton-card')).toHaveCount(24);
  await expect(page.locator('.catalog-skeleton-card').first()).toBeVisible();
  await expect(page.getByLabel('每页条目')).toHaveValue('24');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await page.locator('.catalog-skeleton-surface').first().evaluate(value => getComputedStyle(value).animationName)).toBe('none');
  await page.screenshot({ path: '../../.scratch/metadata-ui/skeleton.png', fullPage: false });
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  pageGate = undefined; releasePage();
  await expect(cards.first()).toContainText('Fixture beaker');
  await expect(cards).toHaveCount(24);
  await expect(page.getByLabel('每页条目')).toHaveValue('24');
  const originalUrl = page.url();
  const released = new URL(originalUrl).searchParams.get('release');
  expect(released).toMatch(/^sha256:/);
  await expect(cards.first().locator('img')).toHaveAttribute('src', /maintenance\/preview\?entry=scenario%2Fscenario%253Aasset/);
  await expect.poll(() => cards.first().locator('img').evaluate(value => (value as HTMLImageElement).naturalWidth)).toBe(1);
  const fixture = await (await request.get(`${api}/e2e/metadata-catalog`)).json();
  expect(fixture.selectedPreview.path).toBe('previews/object.png');
  expect(fixture.requests.estimates).toBe(0);
  pageGate = new Promise(resolve => { releasePage = resolve; });
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByRole('status', { name: '素材加载状态' })).toContainText('第 2 页');
  await expect(page.locator('.catalog-skeleton-card')).toHaveCount(24);
  await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeDisabled();
  failSearch = true;
  pageGate = undefined; releasePage();
  await expect(page.getByRole('alert')).toContainText('第 2 页加载失败');
  await expect(page.locator('.catalog-skeleton-card')).toHaveCount(0);
  await page.getByRole('alert').getByRole('button', { name: '重试', exact: true }).click();
  await expect(cards).toHaveCount(24);
  await expect(page.locator('.catalog-skeleton-card')).toHaveCount(0);
  await expect(cards.first()).not.toHaveAttribute('data-candidate', /asset_000@/);
  expect(new URL(page.url()).searchParams.get('release')).toBe(released);
  await page.getByLabel('页位置').fill('1');
  await page.getByRole('button', { name: '前往', exact: true }).click();
  await expect(page).toHaveURL(/page=1/);
  await expect(cards.first()).toHaveAttribute('data-candidate', /scenario%3Aasset_000@/);
  await page.getByLabel('物体类别').selectOption('container');
  await page.getByLabel('素材标签').fill('sample');
  await page.getByRole('searchbox', { name: '搜索素材' }).fill('样品容器');
  await expect(cards.first()).toContainText('夹具烧杯');
  await page.getByLabel('素材排序').selectOption('name');
  await page.getByRole('button', { name: '☷ 列表' }).click();
  await expect(page.locator('.catalog-result-grid')).toHaveClass(/is-list/);
  await page.reload();
  await expect(page.getByLabel('物体类别')).toHaveValue('container');
  await expect(page.getByLabel('素材标签')).toHaveValue('sample');
  await expect(page.getByRole('searchbox', { name: '搜索素材' })).toHaveValue('样品容器');
  await cards.first().getByRole('button', { name: /查看.*详情/ }).click();
  const detail = page.getByRole('region', { name: '候选详情', exact: true });
  await expect(detail.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
  await expect(detail.getByRole('heading', { name: '技术信息', exact: true })).toBeVisible();
  expect((await (await request.get(`${api}/e2e/metadata-catalog`)).json()).requests.estimates).toBe(0);
  await detail.getByText('完整身份、未知项与来源依据', { exact: true }).click();
  await expect(detail).toContainText('scenario%3Aasset_000@');
  await detail.getByRole('button', { name: '读取依据' }).click();
  await expect(detail.locator('pre')).toContainText('Fixture beaker');
  await detail.getByRole('button', { name: '准备获取 · 查看预算' }).click();
  await expect(detail.getByRole('heading', { name: '获取预算', exact: true })).toBeVisible();
  await expect(detail).toContainText('未知');
  expect((await (await request.get(`${api}/e2e/metadata-catalog`)).json()).requests.estimates).toBe(1);
  await page.screenshot({ path: '../../.scratch/metadata-ui/detail.png', fullPage: true });
  expect((await request.post(`${api}/e2e/metadata-catalog/publish`)).ok()).toBe(true);
  await page.reload();
  await expect(cards.first()).toContainText('Fixture beaker');
  expect(new URL(page.url()).searchParams.get('release')).toBe(released);
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(cards.first()).toContainText('Fixture beaker');
  await page.setViewportSize({ width: 400, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
  await page.screenshot({ path: '../../.scratch/metadata-ui/narrow.png', fullPage: true });
  // Imported local versions share the exact same metadata conditions. One
  // associated container matches; a different associated class and unknown
  // built-in provenance do not satisfy the retained hard filters.
  await page.setViewportSize({ width: 1440, height: 960 });
  const imported: string[] = [];
  for (const matching of [true, false]) {
    const artifact = await request.get(`${api}/e2e/metadata-catalog/local-package`, { params: { matching } });
    const result = await request.post(`${api}/api/v1/library/imports`, {
      data: await artifact.body(), headers: { 'Content-Type': 'application/x-tar' },
    });
    expect(result.ok()).toBe(true);
    imported.push((await result.json()).assetVersion);
  }
  const unknown = await (await request.get(`${api}/api/v1/library/search`, {
    params: { scope: 'local', q: 'asset:robot/fr3@1' },
  })).json();
  expect(unknown.results[0].source.provider).toBeNull();
  await page.getByRole('button', { name: '本地', exact: true }).click();
  await expect(page.getByLabel('物体类别')).toHaveValue('container');
  await expect(page.getByLabel('素材标签')).toHaveValue('sample');
  await expect(page.getByLabel('素材来源')).toHaveValue('scenario');
  await page.getByRole('button', { name: '▦ 卡片', exact: true }).click();
  await expect(page.locator('.asset-card')).toHaveCount(1);
  await expect(page.locator('.asset-card')).toContainText('Metadata associated beaker');
  await expect(page.locator('.asset-card .asset-dots i')).toHaveCount(6);
  const localParams = { scope: 'local', category: 'container', tag: 'sample', provider: 'scenario',
    q: '样品容器', sort: 'name', page: 1, limit: 24, release: released! };
  const local = await (await request.get(`${api}/api/v1/library/search`, { params: localParams })).json();
  expect(local.results.map((value: { id: string }) => value.id)).toEqual([imported[0]]);
  expect(local.counts.matching).toBe(1);
  await page.getByRole('checkbox', { name: '只看缺口', exact: true }).check();
  const gaps = await (await request.get(`${api}/api/v1/library/search`, { params: { ...localParams, gaps: true } })).json();
  await expect(page.locator('.asset-card')).toHaveCount(gaps.counts.returned);
  expect(gaps.results.every((value: { local: { asset: { gapCount: number } } }) => value.local.asset.gapCount > 0)).toBe(true);
  await page.getByRole('checkbox', { name: '只看缺口', exact: true }).uncheck();
  await page.getByRole('button', { name: '☷ 列表', exact: true }).click();
  await expect(page.locator('.asset-list-table tbody tr')).toHaveCount(1);
  await page.reload();
  await expect(page.locator('.asset-list-table tbody tr')).toHaveCount(1);
  await expect(page.getByLabel('素材排序')).toHaveValue('name');
  await page.getByLabel('用途', { exact: true }).selectOption('staticCollision');
  await expect(page.getByRole('heading', { name: '没有匹配的素材', exact: true })).toBeVisible();
  await page.getByLabel('用途', { exact: true }).selectOption('');
  await page.getByLabel('素材标签').fill('does-not-exist');
  await expect(page.getByRole('heading', { name: '没有匹配的素材', exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get('tag')).toBe('does-not-exist');
  await page.goto(`${web}/assets?scope=local`);
  await expect(page.getByLabel('素材来源').locator('option[value="scenario"]')).toHaveCount(1);
  await page.getByLabel('素材来源').selectOption('scenario');
  await page.getByLabel('物体类别').selectOption('container');
  await page.getByLabel('素材标签').fill('sample');
  await expect(page.locator('.asset-card')).toHaveCount(1);
  await expect(page.locator('.asset-card')).toContainText('Metadata associated beaker');

  // Real public task control supplies extraction counters without launching
  // an upstream or Agent worker in this deterministic browser fixture.
  const base = `${api}/api/v1/library/catalog/maintenance`;
  expect((await request.patch(`${base}/collections/scenario`, { data: { enabled: true } })).ok()).toBe(true);
  const started = await request.post(`${base}/runs`, { data: { collectionId: 'scenario', revision: 'd'.repeat(40) } });
  expect(started.ok()).toBe(true);
  const run = (await started.json()).id;
  const entries = [{ upstreamId: 'progress-1' }, { upstreamId: 'progress-2' }, { upstreamId: 'progress-3' }];
  expect((await request.post(`${base}/runs/${run}/inventory`, { data: { entries, offset: 0 } })).ok()).toBe(true);
  expect((await request.post(`${base}/runs/${run}/inventory/complete`, { data: {
    complete: true, extractionComplete: false, revision: 'd'.repeat(40), counts: { entries: 3 },
    usage: { requests: 0, networkBytes: 0, previewBytes: 0 },
  } })).ok()).toBe(true);
  expect((await request.post(`${base}/runs/${run}/extracted`, { data: { entries: [
    { upstreamId: 'progress-1', extractionStatus: 'extracted' },
    { upstreamId: 'progress-2', extractionStatus: 'failed' },
  ] } })).ok()).toBe(true);
  await page.goto(`${web}/assets?scope=online&provider=scenario`);
  const maintenance = page.locator('.catalog-maintenance');
  await maintenance.locator('summary').first().click();
  const collection = maintenance.locator('.catalog-maintenance-collection').filter({ has: page.locator('strong', { hasText: 'scenario' }) });
  await expect(collection).toContainText('已提取 1');
  await expect(collection).toContainText('待提取 1');
  await expect(collection).toContainText('提取失败 1');
  await expect(collection.getByRole('checkbox', { name: '启用此集合', exact: true })).toBeChecked();
  expect((await request.post(`${base}/runs/${run}/cancel`)).ok()).toBe(true);
  await expect(collection).toContainText('已取消');
  await page.getByLabel('元数据整理方式').selectOption('external');
  await page.getByRole('checkbox', { name: '缓存本地 WebP', exact: false }).check();
  const startingExternal = page.waitForResponse(response => response.url().endsWith('/library/catalog/maintenance/runs')
    && response.request().method() === 'POST');
  await collection.getByRole('button', { name: '维护 scenario', exact: true }).click();
  const external = await (await startingExternal).json();
  expect(external.organizer).toBe('external');
  expect(external.previews).toBe(true);
  const externalPath = `${base}/runs/${external.id}`;
  expect((await request.post(`${externalPath}/inventory`, { data: {
    offset: 0, entries: [{ upstreamId: 'external-review', name: { en: 'External bottle', zh: '外部试剂瓶' } }],
  } })).ok()).toBe(true);
  expect((await request.post(`${externalPath}/inventory/complete`, { data: {
    complete: true, revision: 'd'.repeat(40), counts: { entries: 1 },
    usage: { requests: 0, networkBytes: 0, previewBytes: 0 },
  } })).ok()).toBe(true);
  const previews = await (await request.post(`${externalPath}/previews/claim`, { data: {} })).json();
  expect((await request.post(`${externalPath}/previews/submit`, { data: {
    upstreamId: previews.entries[0].upstreamId, inputDigest: previews.entries[0].inputDigest,
    state: 'missing', reason: 'Fixture source provides no object preview',
  } })).ok()).toBe(true);
  expect((await request.post(`${externalPath}/await-agent`)).ok()).toBe(true);
  await expect(collection).toContainText('待外部 Agent 整理');
  const batch = await (await request.post(`${externalPath}/claim`, { data: {} })).json();
  expect((await request.post(`${externalPath}/batches/${batch.id}/submit`, { data: {
    inputKey: batch.inputKey, nativeSessionId: null, usage: { totalTokens: null },
    results: batch.entries.map((entry: { upstreamId: string; inputDigest: string }) => ({
      upstreamId: entry.upstreamId, inputDigest: entry.inputDigest,
      fields: { summary: { zh: '外部 Agent 提交的容器说明' }, category: 'container' },
    })),
  } })).ok()).toBe(true);
  expect((await request.post(`${externalPath}/finish`)).ok()).toBe(true);
  await expect(collection).toContainText('已完成');
  await expect(collection).toContainText('本地缩略图 0 · 缺图 1');
  expect(remote).toEqual([]);
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, {
      python, script: owned, original, outputPath: name => info.outputPath(name),
      context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort],
      prepare: [{ label: 'page:close', run: () => page.goto('about:blank') }],
    });
    finishCleanup(cleanup, original);
  }
});
