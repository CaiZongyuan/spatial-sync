import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { cleanupOwnedProcesses, finishCleanup, type OwnedProcess } from './owned-cleanup';

async function freePort() {
  return new Promise<number>((resolve, reject) => {
    const server = createServer(); server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as { port: number }).port;
      server.close(error => error ? reject(error) : resolve(port));
    });
  });
}

test('candidate acquisition shows real progress, cancel, retry and the local asset link', async ({ page, playwright }, info) => {
  const [apiPort, webPort] = await Promise.all([freePort(), freePort()]);
  const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const owned = resolve('../../tests/fixtures/owned_processes.py'), execute = promisify(execFile);
  const processes: OwnedProcess[] = [], original: unknown[] = [];
  const start = (label: string, executable: string, args: string[], cwd: string, env: NodeJS.ProcessEnv) => {
    const child = spawn(executable, args, { cwd, env, windowsHide: true });
    const record: OwnedProcess = { child, label, output: '', exited: new Promise<void>(resolve => child.once('exit', () => resolve())),
      identity: execute(python, [owned, 'snapshot', String(child.pid)]).then(value => ({ identity: value.stdout }), error => ({ error })) };
    child.stdout?.on('data', chunk => { record.output += chunk; }); child.stderr?.on('data', chunk => { record.output += chunk; });
    child.once('error', error => { record.error = error; }); processes.push(record);
  };
  const env = { ...process.env, SPATIAL_ALLOWED_ORIGINS: web, SPATIAL_API_URL: api, SPATIAL_LIVE_BINDINGS: '',
    LW_LIBRARY: info.outputPath('library'), SPATIAL_STATE_ROOT: info.outputPath('state'),
    SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings') };
  const http = await playwright.request.newContext();
  const remote: string[] = [], errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (/huggingface\.co|hf\.co|cdn-lfs/.test(request.url())) remote.push(request.url());
  });
  const control = async (delaySeconds: number) => {
    const response = await http.post(`${api}/e2e/acquisition-delay`, { data: { delaySeconds } });
    expect(response.ok(), await response.text()).toBe(true);
  };
  try {
    start('acquisition-api', python, ['-m', 'uvicorn', 'acquisition_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('acquisition-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    const roots = await (await http.get(`${api}/e2e/storage-roots`)).json();
    for (const path of Object.values(roots) as string[]) expect(resolve(path).startsWith(resolve(info.outputDir))).toBe(true);

    await control(8);
    await page.goto(`${web}/assets?scope=online&q=box`);
    const card = page.locator('.catalog-card.is-online');
    await expect(card).toHaveCount(1);
    await card.getByRole('button', { name: /查看.*详情/ }).click();
    const detail = page.getByRole('region', { name: '候选详情', exact: true });
    await detail.getByRole('button', { name: '准备获取 · 查看预算' }).click();
    const acquire = detail.getByRole('region', { name: '获取在线候选' });
    await expect(acquire).toContainText('可获取');
    await expect(acquire).toContainText('限额内');
    await expect(acquire).toContainText('批量');
    await acquire.getByRole('button', { name: '开始获取' }).click();
    const record = acquire.locator('.acquisition-record');
    await expect(record).toHaveAttribute('data-acquisition-status', /queued|downloading/, { timeout: 10_000 });
    await expect(record).toContainText('下载中');
    await expect(record).toContainText(/\d+%/);
    await expect(record.getByRole('progressbar', { name: '获取字节进度' })).toHaveAttribute('value', /[1-9][0-9]*/);
    await expect(record).toContainText('实际用量');
    const recordId = await record.getAttribute('data-acquisition-id');
    expect(recordId).toMatch(/^acq-[0-9a-f]{32}$/);

    await record.getByRole('button', { name: '取消获取' }).click();
    await expect(record).toHaveAttribute('data-acquisition-status', 'cancelled', { timeout: 15_000 });
    await expect(record.getByRole('link', { name: '打开本地素材' })).toHaveCount(0);

    await control(0.02);
    await record.getByRole('button', { name: '重试获取' }).click();
    await expect(record).toHaveAttribute('data-acquisition-status', 'succeeded', { timeout: 20_000 });
    await expect(record).toContainText('已发布的本地版本');
    await expect(record).toContainText('第 2 次');
    const link = record.getByRole('link', { name: '打开本地素材' });
    await expect(link).toHaveAttribute('href', /\/assets\/environment\/cardboard-box\/1$/);

    await page.setViewportSize({ width: 400, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
    await page.setViewportSize({ width: 1440, height: 960 });
    await link.click();
    await expect(page).toHaveURL(new RegExp(`/assets/environment/cardboard-box/1$`));
    await expect(page.getByRole('heading', { name: 'Cardboard box acquisition fixture', exact: true })).toBeVisible();

    await page.goto(`${web}/assets?scope=online`);
    const records = page.getByRole('region', { name: '获取记录' });
    await records.getByRole('button', { name: '刷新获取记录' }).click();
    await expect(records.locator('.acquisition-record')).toHaveCount(1);
    await expect(records.locator('.acquisition-record')).toContainText('已成功');
    await expect(records.locator('.acquisition-record')).toContainText(recordId!);
    await expect(records.getByRole('link', { name: '打开本地素材' })).toHaveAttribute(
      'href', /\/assets\/environment\/cardboard-box\/1$/);
    await page.setViewportSize({ width: 400, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
    await page.screenshot({ path: info.outputPath('acquisition-records-400.png'), fullPage: true });
    expect(remote).toEqual([]); expect(errors).toEqual([]);
    await writeFile(info.outputPath('acquisition-receipt.json'), JSON.stringify({
      recordId, api, web, scope: 'GPU-free fixture runner; no network, worker or third-party bytes', remote, errors,
    }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script: owned, original,
      outputPath: name => info.outputPath(name), context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'acquisition:cancel-active', run: async () => {
          const listing = await (await http.get(`${api}/api/v1/library/acquisitions`)).json();
          for (const record of listing.acquisitions as { id: string; status: string }[]) {
            if (!['succeeded', 'failed', 'cancelled'].includes(record.status))
              expect((await http.post(`${api}/api/v1/library/acquisitions/${record.id}/cancel`)).ok()).toBe(true);
          }
        } },
        { label: 'page:close', run: () => page.goto('about:blank') },
        { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
