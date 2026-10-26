import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { cleanupOwnedProcesses, finishCleanup, type OwnedProcess } from './owned-cleanup';

async function freePort() {
  return new Promise<number>((done, reject) => {
    const server = createServer(); server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as { port: number }).port;
      server.close(error => error ? reject(error) : done(port));
    });
  });
}

test('lightweight indexing, visible previews, retries and failed-source preservation', async ({ page, playwright }, info) => {
  const [apiPort, webPort] = await Promise.all([freePort(), freePort()]);
  const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const owned = resolve('../../tests/fixtures/owned_processes.py'), execute = promisify(execFile);
  const processes: OwnedProcess[] = [], original: unknown[] = [], errors: string[] = [], remote: string[] = [];
  const start = (label: string, executable: string, args: string[], cwd: string, env: NodeJS.ProcessEnv) => {
    const child = spawn(executable, args, { cwd, env, windowsHide: true });
    const record: OwnedProcess = { child, label, output: '', exited: new Promise<void>(done => child.once('exit', () => done())),
      identity: execute(python, [owned, 'snapshot', String(child.pid)]).then(value => ({ identity: value.stdout }), error => ({ error })) };
    child.stdout?.on('data', chunk => { record.output += chunk; }); child.stderr?.on('data', chunk => { record.output += chunk; });
    child.once('error', error => { record.error = error; }); processes.push(record);
  };
  const env = { ...process.env, SPATIAL_ALLOWED_ORIGINS: web, SPATIAL_API_URL: api, SPATIAL_LIVE_BINDINGS: '',
    LW_LIBRARY: info.outputPath('library'), SPATIAL_STATE_ROOT: info.outputPath('state'),
    SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings') };
  const http = await playwright.request.newContext();
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/huggingface\.co|hf\.co|cdn-lfs/.test(request.url())) remote.push(request.url()); });
  const control = async (delaySeconds: number, failListing = false) =>
    expect((await http.post(`${api}/e2e/catalog-source`, { data: { delaySeconds, failListing } })).ok()).toBe(true);
  try {
    start('catalog-sync-api', python, ['-m', 'uvicorn', 'catalog_sync_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('catalog-sync-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    await control(2);
    await page.goto(`${web}/assets?scope=online`);
    const sync = page.getByRole('region', { name: '在线目录同步' });
    await expect(sync).toContainText('预览与详情按需加载');
    await expect(page.locator('.catalog-card.is-online')).toHaveCount(5);
    await sync.getByRole('button', { name: '更新目录', exact: true }).click();
    await sync.getByRole('button', { name: '取消更新', exact: true }).click();
    await expect(sync).toContainText('已取消');
    await expect(page.locator('.catalog-card.is-online')).toHaveCount(5);
    await control(0);
    await sync.getByRole('button', { name: '重试更新' }).click();
    await expect(sync).toContainText('目录已更新');
    await expect(sync).toContainText('2 项在线素材');
    await page.getByRole('button', { name: '查看最新目录', exact: true }).click();
    await expect(page.locator('.catalog-card.is-online')).toHaveCount(2);
    const first = page.locator('.catalog-card.is-online').first();
    await expect(first.locator('img')).toHaveAttribute('src', /\/api\/v1\/library\/catalog\/entries\/.+\/preview$/);
    await expect.poll(() => first.locator('img').evaluate(img => (img as HTMLImageElement).naturalWidth)).toBe(1);
    // A seed card keeps its React key across publication; it must attach a new observer.
    const retained = page.locator('.catalog-card[data-candidate^="simready/box_cardboard_a01@"]');
    await retained.scrollIntoViewIfNeeded();
    await expect.poll(() => retained.locator('img').evaluate(img => (img as HTMLImageElement).naturalWidth)).toBe(1);
    await page.getByRole('searchbox', { name: '搜索素材' }).fill('CT_fixture_a01');
    await expect(page.locator('.catalog-card.is-online')).toHaveCount(1);
    await expect(page.locator('.catalog-card.is-online')).toContainText('未审查／上游声明');
    await page.locator('.catalog-card.is-online').getByRole('button', { name: /查看.*详情/ }).click();
    await expect(page.getByRole('region', { name: '候选详情', exact: true })).toContainText('尚未审查；声明不等于获取准入');
    await sync.getByRole('button', { name: '更新目录', exact: true }).click();
    await expect(sync).toContainText('目录已更新');
    const published = (await (await http.get(`${api}/api/v1/library/catalog/sync`)).json()).snapshot.id;
    await control(0, true);
    await sync.getByRole('button', { name: '更新目录', exact: true }).click();
    await expect(sync).toContainText('更新失败');
    await expect(page.locator('.catalog-card.is-online')).toHaveCount(1);
    expect((await (await http.get(`${api}/api/v1/library/catalog/sync`)).json()).snapshot.id).toBe(published);
    // With no browser consuming cards, a complete new index downloads no package content.
    await page.goto('about:blank');
    await http.post(`${api}/e2e/catalog-source`, { data: { count: 30, delaySeconds: 0, clearRequests: true } });
    const created = await (await http.post(`${api}/api/v1/library/catalog/sync`)).json();
    await expect.poll(async () => (await (await http.get(`${api}/api/v1/library/catalog/sync/${created.id}`)).json()).status).toBe('succeeded');
    const indexed = (await (await http.get(`${api}/e2e/catalog-source`)).json()).requests as string[];
    expect(indexed).toHaveLength(31);
    expect(indexed.filter(path => path.includes('/resolve/'))).toEqual([]);
    const requested: string[] = [];
    page.on('request', request => { if (/\/catalog\/entries\/.+\/preview/.test(request.url())) requested.push(request.url()); });
    await page.goto(`${web}/assets?scope=online`);
    const cards = page.locator('.catalog-card.is-online');
    await expect.poll(() => cards.count()).toBeGreaterThan(3);
    const last = cards.last();
    expect(await last.evaluate(card => card.getBoundingClientRect().top > window.innerHeight)).toBe(true);
    await expect(last.locator('img')).toHaveCount(0);
    await expect.poll(() => cards.first().locator('img').evaluate(img => (img as HTMLImageElement).naturalWidth)).toBe(1);
    expect(requested.length).toBeLessThan(await cards.count());
    await last.scrollIntoViewIfNeeded();
    await expect.poll(() => last.locator('img').evaluate(img => (img as HTMLImageElement).naturalWidth)).toBe(1);
    const content = (await (await http.get(`${api}/e2e/catalog-source`)).json()).requests as string[];
    expect(content.filter(path => path.endsWith('com.nvidia.simready.packaging.json'))).toHaveLength(0);
    expect(content.filter(path => path.endsWith('.png')).length).toBeLessThan(30);
    await page.getByLabel('素材来源').selectOption('simready');
    await expect(page).toHaveURL(/provider=simready/);
    await expect(cards.first()).toBeVisible();
    await page.setViewportSize({ width: 400, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
    await page.screenshot({ path: info.outputPath('catalog-sync-400.png'), fullPage: true });
    expect(errors).toEqual([]); expect(remote).toEqual([]);
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script: owned, original,
      outputPath: name => info.outputPath(name), context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'page:close', run: () => page.goto('about:blank') },
        { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
