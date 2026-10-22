import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { cleanupOwnedProcesses, finishCleanup, type OwnedProcess } from './owned-cleanup';
import type {} from '../../apps/web/src/components/viewer/SceneDiagnostics';

const key = '/Real/Incubator2';
async function port() {
  return new Promise<number>((resolve, reject) => {
    const server = createServer(); server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address() as { port: number };
      server.close(error => error ? reject(error) : resolve(address.port));
    });
  });
}

test('real Live layout uses the verified capture and maps floating labels and geometry to the real drawer', async ({ page, playwright }, info) => {
  const [apiPort, webPort] = await Promise.all([port(), port()]);
  const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const script = resolve('../../tests/fixtures/owned_processes.py'), execute = promisify(execFile);
  const processes: OwnedProcess[] = [], original: unknown[] = [];
  const env = { ...process.env, SPATIAL_REMOTE: '0', SPATIAL_ALLOWED_ORIGINS: web, SPATIAL_API_URL: api,
    LW_LIBRARY: info.outputPath('library'), SPATIAL_STATE_ROOT: info.outputPath('state'),
    SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings'), SPATIAL_LIVE_BINDINGS: '', SPATIAL_TEST_LIVE_LAYOUT: '1' };
  const start = (label: string, executable: string, args: string[], cwd: string) => {
    const child = spawn(executable, args, { cwd, env, windowsHide: true });
    const record = { child, label, output: '', error: undefined as Error | undefined,
      exited: new Promise<void>(resolve => child.once('exit', () => resolve())),
      identity: execute(python, [script, 'snapshot', String(child.pid)])
        .then(value => ({ identity: value.stdout }), error => ({ error })) };
    child.stdout?.on('data', data => { record.output += data; }); child.stderr?.on('data', data => { record.output += data; });
    child.once('error', error => { record.error = error; }); processes.push(record);
  };
  const http = await playwright.request.newContext();
  const forbidden: string[] = [];
  page.on('websocket', socket => forbidden.push(socket.url()));
  page.on('request', request => {
    if (/\/sessions\/[^/]+\/(scene|catalog|state|entities|ws)/.test(new URL(request.url()).pathname)) forbidden.push(request.url());
  });
  let ready = false;
  try {
    start('layout-api', python, ['-m', 'uvicorn', 'live_operations_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'));
    start('layout-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []),
      '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd());
    await expect.poll(async () => {
      for (const record of processes) if (record.error || record.child.exitCode !== null) throw record.error ?? new Error(record.output);
      try { return (await http.get(`${api}/api/v1/live`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    ready = true;
    const live = await (await http.get(`${api}/api/v1/live`)).json();
    const detail = await (await http.get(`${api}/api/v1/library/scenes/lab-demo/2`)).json();
    expect(live).toMatchObject({ layout: 'lab-demo@2', layoutContentHash: detail.contentHash, layoutError: null });
    expect(live.placements[key]).toBe('/World/Lab/Incubator');
    expect(detail.preview).toMatchObject({ status: 'available', sceneVersion: 'lab-demo@2', sceneContentHash: detail.contentHash });
    await page.goto(`${web}/live`);
    const drawer = page.getByRole('complementary', { name: '设备抽屉' });
    await expect(drawer).toHaveAttribute('data-connection', 'connected');
    await expect(page.locator('canvas')).toHaveCount(1);
    const label = page.getByTestId(`device-label-${key}`);
    await expect(label).toBeVisible();
    const hit = await label.evaluate(element => {
      const bounds = element.getBoundingClientRect();
      return document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)?.closest('button') === element;
    });
    expect(hit).toBe(true); await label.click();
    await expect(drawer).toContainText(key);
    await expect(drawer.getByTestId(`reading-${key}-temperatureC`)).toContainText('37');
    await expect(label).toHaveAttribute('aria-pressed', 'true');
    await drawer.getByRole('button', { name: '× 关闭' }).click();
    const projected = await page.evaluate(() => window.__spatialRenderDebug?.projectEntity('/World/Lab/Incubator'));
    expect(projected).toBeTruthy();
    const hits = await page.evaluate(point => window.__spatialRenderDebug?.pick(point.x, point.y), projected!);
    expect(hits?.some(hit => hit.key === '/World/Lab/Incubator')).toBe(true);
    await page.mouse.click(projected!.x, projected!.y);
    await expect(drawer).toContainText(key);
    await http.post(`${api}/e2e/live/fault`, { data: { mode: 'heat' } });
    await expect(drawer.getByTestId(`reading-${key}-temperatureC`)).toContainText('41');
    await expect(label).toContainText('41');
    await page.getByRole('button', { name: '读数', exact: true }).click();
    await expect(label.locator('span')).toHaveCount(0);
    await expect(drawer.getByTestId(`reading-${key}-temperatureC`)).toContainText('41');
    const counters = await (await http.get(`${api}/e2e/live`)).json();
    expect(counters).toMatchObject({ sourceCalls: 0, sessions: 0 }); expect(forbidden).toEqual([]);
    await page.screenshot({ path: info.outputPath('real-live-layout-1440.png'), fullPage: true });
    await writeFile(info.outputPath('layout-evidence.json'), JSON.stringify({ live, preview: detail.preview, hit, projected, hits, counters, forbidden }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script, original,
      outputPath: name => info.outputPath(name), context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort],
      prepare: [
        { label: 'live:close', run: async () => {
          if (!ready) return;
          const response = await http.post(`${api}/e2e/live/close`);
          expect(response.ok()).toBe(true);
          expect(await response.json()).toMatchObject({ sourceCalls: 0, sessions: 0, peerTasksDone: true });
        } },
        { label: 'page:close', run: () => page.goto('about:blank') },
        { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
