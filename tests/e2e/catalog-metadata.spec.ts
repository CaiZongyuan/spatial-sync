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
  await page.goto(`${web}/assets?scope=online&provider=scenario`);
  const cards = page.locator('.catalog-card.is-online');
  await expect(cards.first()).toContainText('Fixture beaker');
  await expect(page.getByLabel('每页条目')).toHaveValue('24');
  const originalUrl = page.url();
  const released = new URL(originalUrl).searchParams.get('release');
  expect(released).toMatch(/^sha256:/);
  await expect(cards.first().locator('img')).toHaveAttribute('src', /maintenance\/preview\?entry=scenario%2Fscenario%253Aasset/);
  await expect.poll(() => cards.first().locator('img').evaluate(value => (value as HTMLImageElement).naturalWidth)).toBe(1);
  const fixture = await (await request.get(`${api}/e2e/metadata-catalog`)).json();
  expect(fixture.selectedPreview.path).toBe('previews/object.png');
  expect(fixture.requests.estimates).toBe(0);
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page).toHaveURL(/page=2/);
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
