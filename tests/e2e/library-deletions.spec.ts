import { expect, test as base } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { cp, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';
import { createServer } from 'node:net';

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const test = base.extend<{ isolatedAPI: string }>({
  isolatedAPI: async ({ playwright, page }, use, info) => {
    const port = await new Promise<number>((resolve, reject) => {
      const reservation = createServer();
      reservation.once('error', reject);
      reservation.listen(0, '127.0.0.1', () => {
        const { port } = reservation.address() as { port: number };
        reservation.close(error => error ? reject(error) : resolve(port));
      });
    });
    const api = `http://127.0.0.1:${port}`;
    const python = resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
    const child = spawn(python, ['-m', 'uvicorn', 'approval_gateway:app', '--app-dir', 'tests/fixtures', '--host', '127.0.0.1', '--port', String(port)], {
      cwd: resolve('../..'), windowsHide: true, env: { ...process.env, SPATIAL_REMOTE: '0',
        LW_LIBRARY: info.outputPath('api-library'), SPATIAL_STATE_ROOT: info.outputPath('api-state'),
        SPATIAL_RECORDINGS_ROOT: info.outputPath('api-recordings'),
      },
    });
    let output = '', launchError: Error | undefined;
    child.stdout.on('data', data => { output += data.toString(); });
    child.stderr.on('data', data => { output += data.toString(); });
    child.once('error', error => { launchError = error; });
    const exited = new Promise<boolean>(resolve => child.once('exit', () => resolve(true)));
    const health = await playwright.request.newContext();
    try {
      const deadline = Date.now() + 15_000;
      let ready = false;
      while (Date.now() < deadline) {
        if (launchError || child.exitCode !== null) throw launchError ?? new Error(output);
        if (output.includes('Uvicorn running on')) {
          try { ready = (await health.get(`${api}/api/v1/health`, { timeout: 500 })).ok(); } catch { /* startup */ }
          if (ready) break;
        }
        await delay(50);
      }
      expect(ready, output).toBe(true);
      // Only forward real HTTP bytes; shared cases deliberately retain invalid packages.
      await page.route('**/api/v1/**', async route => {
        const url = new URL(route.request().url());
        const response = await route.fetch({ url: api + url.pathname + url.search });
        await route.fulfill({ response });
      });
      await use(api);
    } finally {
      await page.unrouteAll({ behavior: 'wait' }).catch(() => {});
      await health.dispose().catch(() => {});
      // This current ChildProcess owns a single uvicorn (no worker/Runner is launched).
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
      let stopped = await Promise.race([exited, delay(3_000).then(() => false)]);
      if (!stopped) { child.kill('SIGKILL'); stopped = await Promise.race([exited, delay(3_000).then(() => false)]); }
      await writeFile(info.outputPath('isolated-api.log'), output);
      await writeFile(info.outputPath('isolated-api-receipt.json'), JSON.stringify({ api, pid: child.pid, source: resolve('../..'), python, stopped, exitCode: child.exitCode, signalCode: child.signalCode }));
      expect(stopped, 'Owned isolated API must exit').toBe(true);
    }
  },
});
let ownedVersion: { api: string; id: string; target: string; contentHash: string } | undefined;

test.afterEach(async ({ playwright }, info) => {
  if (!ownedVersion) return;
  // Worker-owned API context survives the page/test request context closing on a case timeout.
  const cleanup = await playwright.request.newContext({ extraHTTPHeaders: { 'X-Actor': 'user:e2e-cleanup' } });
  const owned = ownedVersion;
  const api = owned.api;
  try {
    const current = await cleanup.get(api + owned.target);
    if (current.status() === 404) return;
    expect(current.ok()).toBe(true);
    const actual = await current.json();
    expect(actual.id).toBe(owned.id);
    expect(actual.contentHash).toBe(owned.contentHash);
    const pending = await cleanup.delete(api + owned.target);
    expect(pending.status()).toBe(202);
    const { approvalId } = await pending.json();
    const removed = await cleanup.post(`${api}/api/v1/approvals/${approvalId}/decision`, {
      data: { decision: 'approve', note: '清理本测试拥有的演示版本' },
    });
    expect(removed.ok()).toBe(true);
    expect((await cleanup.get(api + owned.target)).status()).toBe(404);
    await info.attach('deletion-cleanup.json', { body: JSON.stringify({ owned, result: await removed.json() }), contentType: 'application/json' });
  } finally { ownedVersion = undefined; await cleanup.dispose(); }
});

test('exact user asset requests a human decision, then disappears from the library', async ({ page, request, isolatedAPI: api }, info) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  const directory = info.outputPath('deletion-asset');
  await cp(resolve('../../tests/fixtures/asset-library/gap-cylinder'), directory, { recursive: true });
  const metadata = await readFile(resolve(directory, 'asset.yaml'), 'utf8');
  await writeFile(resolve(directory, 'asset.yaml'), metadata.replace('labware/ui-cylinder', 'labware/deletion-browser'));
  const executable = resolve(process.platform === 'win32' ? '../../.venv/Scripts/lw.exe' : '../../.venv/bin/lw');
  const { stdout } = await promisify(execFile)(executable, ['asset', 'import', directory, '--json'], {
    env: { ...process.env, LW_URL: api, LW_SESSION: '' }, encoding: 'utf8', timeout: 30_000,
  });
  const imported = JSON.parse(stdout);
  const revision = imported.assetVersion.split('@')[1];
  const target = `/api/v1/library/assets/labware/deletion-browser/${revision}`;
  ownedVersion = { api, id: imported.assetVersion, target, contentHash: imported.contentHash };
  await writeFile(info.outputPath('owned-version.json'), JSON.stringify(ownedVersion));
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const health = await (await request.get(`${api}/api/v1/health`)).json();
  const before = await (await request.get(api + target)).json();
  expect(before.contentHash).toBe(imported.contentHash);
  const files = await Promise.all(Object.entries(before.files as Record<string, string>).map(async ([name, url]) => {
    const response = await request.get(api + url);
    expect(response.ok()).toBe(true);
    return { name, url, sha256: createHash('sha256').update(await response.body()).digest('hex') };
  }));
  await page.goto(`/assets/labware/deletion-browser/${revision}`);
  await expect(page.getByRole('heading', { name: 'UI Cylinder', exact: true })).toBeVisible();
  const panel = page.getByRole('region', { name: '删除库版本' });
  const pendingResponse = page.waitForResponse(response => new URL(response.url()).pathname === target && response.request().method() === 'DELETE');
  await panel.getByRole('button', { name: '申请删除此版本' }).click();
  const pending = await (await pendingResponse).json();
  expect(pending.code).toBe('pending_approval');
  await expect(panel.getByRole('status')).toContainText('版本尚未删除');
  expect((await request.get(api + target)).ok()).toBe(true);
  await panel.getByRole('link', { name: '查看待审批' }).click();
  const card = page.getByRole('article', { name: /删除版本：删除库版本 labware\/deletion-browser@/ });
  await expect(card).toBeVisible();
  await expect(card).toContainText('已检查，无引用');
  await expect(card).toContainText(imported.assetVersion);
  const pendingRecord = await (await request.get(`${api}/api/v1/approvals/${pending.approvalId}`)).json();
  expect(pendingRecord.details.contentHash).toBe(before.contentHash);
  expect(pendingRecord.details.inspection.complete).toBe(true);
  await card.getByLabel('备注').fill('仅删除已核对的不可变版本');
  await page.screenshot({ path: info.outputPath('deletion-pending-1600.png'), fullPage: true });
  await card.getByRole('button', { name: '批准', exact: true }).click();
  await expect(card).toContainText('版本已删除，索引已更新');
  await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('deletion-committed-1600.png'), fullPage: true });
  await expect(card.getByRole('button', { name: '撤销', exact: true })).toHaveCount(0);
  expect((await request.get(api + target)).status()).toBe(404);
  const filesAfter = await Promise.all(files.map(async file => ({ ...file, status: (await request.get(api + file.url)).status() })));
  expect(filesAfter.every(file => file.status === 404)).toBe(true);
  const approved = await (await request.get(`${api}/api/v1/approvals/${pending.approvalId}`)).json();
  expect(approved.decisions).toHaveLength(1);
  expect(approved.details.execution.status).toBe('committed');
  await info.attach('deletion-independent-http.json', { body: JSON.stringify({ health, imported, before, files, pendingRecord, approved, filesAfter }, null, 2), contentType: 'application/json' });
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Assets', exact: true }).click();
  await page.getByRole('searchbox', { name: '搜索素材' }).fill('deletion-browser');
  await expect(page.getByRole('heading', { name: '没有匹配的素材' })).toBeVisible();
  expect(errors).toEqual([]);
  expect((await (await request.get(`${api}/api/v1/health`)).json()).sessions).toEqual([]);
});
