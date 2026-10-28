import { expect, test as base } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { cp, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';
import { createServer } from 'node:net';
import { cleanupOwnedProcesses, finishCleanup, type OwnedProcess } from './owned-cleanup';

async function freePort() {
  return new Promise<number>((resolve, reject) => {
    const reservation = createServer();
    reservation.once('error', reject);
    reservation.listen(0, '127.0.0.1', () => {
      const { port } = reservation.address() as { port: number };
      reservation.close(error => error ? reject(error) : resolve(port));
    });
  });
}

const test = base.extend<{ isolatedAPI: { api: string; web: string } }>({
  isolatedAPI: async ({ playwright, page }, use, info) => {
    const [port, webPort] = await Promise.all([freePort(), freePort()]);
    const api = `http://127.0.0.1:${port}`;
    const web = `http://127.0.0.1:${webPort}`;
    const python = resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
    const owned = resolve('../../tests/fixtures/owned_processes.py'), execute = promisify(execFile);
    const processes: OwnedProcess[] = [];
    const env = { ...process.env, SPATIAL_REMOTE: '0', SPATIAL_ALLOWED_ORIGINS: web, SPATIAL_API_URL: api,
      LW_LIBRARY: info.outputPath('api-library'), SPATIAL_STATE_ROOT: info.outputPath('api-state'),
      SPATIAL_RECORDINGS_ROOT: info.outputPath('api-recordings') };
    const start = (label: string, executable: string, args: string[], cwd: string) => {
      const child = spawn(executable, args, { cwd, windowsHide: true, env });
      const record: OwnedProcess = { child, label, output: '', exited: new Promise<void>(resolve => child.once('exit', () => resolve())),
        identity: execute(python, [owned, 'snapshot', String(child.pid)]).then(value => ({ identity: value.stdout }), error => ({ error })) };
      child.stdout.on('data', data => { record.output += data.toString(); });
      child.stderr.on('data', data => { record.output += data.toString(); });
      child.once('error', error => { record.error = error; }); processes.push(record);
    };
    const health = await playwright.request.newContext();
    try {
      start('isolated-api', python, ['-m', 'uvicorn', 'approval_gateway:app', '--app-dir', 'tests/fixtures',
        '--host', '127.0.0.1', '--port', String(port)], resolve('../..'));
      start('isolated-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
        ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd());
      await expect.poll(async () => {
        for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
        try { return (await health.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await health.get(web, { timeout: 500 })).ok(); }
        catch { return false; }
      }, { timeout: 15_000 }).toBe(true);
      // The actual Vite proxy isolates HTTP without a second route.fetch/fulfill owner.
      await use({ api, web });
    } finally {
      const cleanup = await cleanupOwnedProcesses(processes, { python, script: owned, original: [], outputPath: name => info.outputPath(name),
        context: { api, web, source: resolve('../..'), python }, ports: [port, webPort], prepare: [
          { label: 'page:close', run: () => page.goto('about:blank') },
          { label: 'health:dispose', run: () => health.dispose() },
        ] });
      finishCleanup(cleanup, []);
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

test('exact user asset moves directly to recovery storage and restores the same bytes', async ({ page, request, isolatedAPI: { api, web } }, info) => {
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
  await page.goto(`${web}/assets/labware/deletion-browser/${revision}`);
  await expect(page.getByRole('heading', { name: 'UI Cylinder', exact: true })).toBeVisible();
  const panel = page.getByRole('region', { name: '删除库版本' });
  const pendingResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/library/deletions' && response.request().method() === 'POST');
  await panel.getByRole('button', { name: '移到回收站', exact: true }).click();
  const pending = await (await pendingResponse).json();
  expect(pending.schema).toBe('lw.library-deletion/1');
  await expect(panel.getByRole('status')).toContainText('版本已移到回收站');
  expect((await request.get(api + target)).status()).toBe(404);
  const removed = await (await request.get(`${api}/api/v1/library/deletions/${pending.id}`)).json();
  expect(removed.status).toBe('committed'); expect(removed.retainedBytes).toBeGreaterThan(0);
  expect((await (await request.get(`${api}/api/v1/approvals`)).json()).pendingCount).toBe(0);
  for (const file of files) expect((await request.get(api + file.url)).status()).toBe(404);
  await page.screenshot({ path: info.outputPath('direct-deletion-committed.png'), fullPage: true });
  await page.getByRole('link', { name: 'Recovery', exact: true }).click();
  const recovery = page.getByRole('article', { name: imported.assetVersion, exact: true });
  await expect(recovery.getByText('可恢复', { exact: true })).toBeVisible();
  await expect(recovery.getByText('内容哈希', { exact: true })).not.toBeVisible();
  await recovery.getByText('记录详情', { exact: true }).click();
  await expect(recovery.getByText(imported.contentHash, { exact: true })).toBeVisible();
  await recovery.getByRole('button', { name: '恢复此版本', exact: true }).click();
  await expect(recovery.getByRole('status')).toContainText('原始版本已恢复');
  await expect(recovery.getByText('已恢复', { exact: true })).toBeVisible();
  await expect(recovery.getByRole('button', { name: '恢复此版本', exact: true })).toHaveCount(0);
  expect((await (await request.get(api + target)).json()).contentHash).toBe(imported.contentHash);
  for (const file of files) {
    const restored = await request.get(api + file.url);
    expect(restored.ok()).toBe(true);
    expect(createHash('sha256').update(await restored.body()).digest('hex')).toBe(file.sha256);
  }
  const record = await (await request.get(`${api}/api/v1/library/deletions/${pending.id}`)).json();
  expect(record.status).toBe('restored'); expect(record.retainedBytes).toBe(0);
  await info.attach('direct-removal-and-restore.json', { body: JSON.stringify({ health, imported, files, removed, record }), contentType: 'application/json' });
  expect(errors).toEqual([]);
  expect((await (await request.get(`${api}/api/v1/health`)).json()).sessions).toEqual([]);
});

test('recovery page retries failed reads and resets an expired page without stale navigation', async ({ page }) => {
  let initialFailure = true, reset = false;
  let releaseRetry!: () => void;
  const retryGate = new Promise<void>(resolve => { releaseRetry = resolve; });
  const cursors: (string | null)[] = [];
  await page.route('**/api/v1/library/deletions?*', async route => {
    const cursor = new URL(route.request().url()).searchParams.get('cursor');
    cursors.push(cursor);
    if (initialFailure) return route.fulfill({ status: 503, json: { detail: { message: '暂时无法读取记录' } } });
    if (cursor) return route.fulfill({ status: 409, json: { detail: { message: '记录列表已变化，请从第一页重新读取' } } });
    if (!reset) {
      await retryGate;
      return route.fulfill({ json: { deletions: [{
        schema: 'lw.library-deletion/1', id: 'recovery-ui-record', resource: 'scene',
        versionId: 'a-long-scene-version-name-for-recovery-layout@12', contentHash: 'a'.repeat(64),
        requestId: 'recovery-ui-request', createdAtMs: 1793145600000, retainedBytes: 0, status: 'restored',
      }], pagination: { total: 21, nextCursor: 'expired-cursor' } } });
    }
    return route.fulfill({ json: { deletions: [], pagination: { total: 0, nextCursor: null } } });
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/trash');
  await expect(page.getByRole('heading', { name: '暂时无法读取回收站' })).toBeVisible();
  await expect(page.getByRole('status')).toHaveCount(0);
  initialFailure = false;
  await page.getByRole('button', { name: '重试当前页' }).click();
  try {
    await expect(page.getByRole('status')).toContainText('正在读取回收站');
    await expect(page.getByRole('button', { name: '读取中…' })).toBeDisabled();
    await expect(page.getByRole('button', { name: '下一页' })).toBeDisabled();
    expect(await page.locator('.trash-skeleton i').first().evaluate(element => getComputedStyle(element).animationName)).toBe('none');
  } finally { releaseRetry(); }
  await expect(page.getByText('已恢复', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByText('记录详情', { exact: true }).click();
  await expect(page.getByText('a'.repeat(64), { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', { name: '下一页' }).click();
  await expect(page.getByRole('heading', { name: '暂时无法读取回收站' })).toBeVisible();
  await expect(page.getByRole('button', { name: '下一页' })).toBeDisabled();
  reset = true;
  await page.getByRole('button', { name: '返回第一页' }).click();
  await expect(page.getByRole('heading', { name: '回收站为空' })).toBeVisible();
  await expect(page.getByRole('link', { name: '浏览场景库' })).toBeVisible();
  await expect(page.getByRole('button', { name: '上一页' })).toBeDisabled();
  expect(cursors.filter(Boolean)).toEqual(['expired-cursor']);
  expect(cursors.at(-1)).toBeNull();
});
