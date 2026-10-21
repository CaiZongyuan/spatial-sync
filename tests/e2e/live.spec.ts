import { test, diagnosticResize, diagnosticWidth, diagnosticBeforeBlank } from './live-diagnostic';
import { expect, type Frame, type Request } from '@playwright/test';
import { execFile, spawn, type ChildProcess } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { createConnection, createServer } from 'node:net';
import { resolve } from 'node:path';
import { promisify } from 'node:util';

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const incubator = '/World/Lab/Incubator', sensor = '/World/Lab/EnvironmentSensor';
async function port() {
  return new Promise<number>((resolve, reject) => {
    const server = createServer(); server.once('error', reject);
    server.listen(0, '127.0.0.1', () => { const { port } = server.address() as { port: number }; server.close(error => error ? reject(error) : resolve(port)); });
  });
}
async function closed(port: number) {
  return new Promise<boolean>(resolve => {
    const connection = createConnection({ host: '127.0.0.1', port });
    connection.once('connect', () => { connection.destroy(); resolve(false); });
    connection.once('error', () => resolve(true));
    connection.setTimeout(1000, () => { connection.destroy(); resolve(false); });
  });
}

test('real simulated devices raise, acknowledge, retain actual history and expose stale readings in both themes', async ({ page, playwright }, info) => {
  test.setTimeout(90_000);
  // This case owns both processes and all library/state/recording files, even
  // when the rest of the suite is connected to a separately owned API.
  const [apiPort, webPort] = await Promise.all([port(), port()]);
  const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const processes: { child: ChildProcess; output: string; exited: Promise<void>; label: string; error?: Error }[] = [];
  const start = (label: string, executable: string, args: string[], cwd: string, environment: NodeJS.ProcessEnv) => {
    const child = spawn(executable, args, { cwd, env: environment, windowsHide: true });
    const record = { child, output: '', exited: new Promise<void>(resolve => child.once('exit', () => resolve())), label, error: undefined as Error | undefined };
    child.stdout?.on('data', data => { record.output += data; }); child.stderr?.on('data', data => { record.output += data; });
    child.once('error', error => { record.error = error; }); processes.push(record); return record;
  };
  const env = { ...process.env, SPATIAL_REMOTE: '0', SPATIAL_ALLOWED_ORIGINS: web,
    LW_LIBRARY: info.outputPath('library'), SPATIAL_STATE_ROOT: info.outputPath('state'), SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings'), SPATIAL_API_URL: api };
  for (const directory of [env.LW_LIBRARY, env.SPATIAL_STATE_ROOT, env.SPATIAL_RECORDINGS_ROOT]) {
    expect(resolve(directory).startsWith(resolve(info.outputDir) + (process.platform === 'win32' ? '\\' : '/'))).toBe(true);
  }
  const http = await playwright.request.newContext();
  let sessionId: string | undefined;
  try {
    start('api', python, ['-m', 'uvicorn', 'live_gateway:app', '--app-dir', 'tests/fixtures', '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []),
      '--host', '127.0.0.1', '--port', String(webPort)], process.cwd(), env);
    await expect.poll(async () => {
      for (const record of processes) if (record.error || record.child.exitCode !== null) throw record.error ?? new Error(record.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); } catch { return false; }
    }, { timeout: 20000 }).toBe(true);
    const { stdout } = await promisify(execFile)(python, [resolve('../../tests/fixtures/live_scene_package.py')], { encoding: 'buffer' });
    const imported = await http.post(`${api}/api/v1/library/imports`, { headers: { 'Content-Type': 'application/x-tar' }, data: stdout });
    expect(imported.ok(), await imported.text()).toBe(true);
    const version = (await imported.json()).sceneVersion;
    expect(version).toMatch(/^lab-demo@\d+$/);
    const created = await http.post(`${api}/api/v1/sessions`, { data: { sceneVersion: version, source: 'synthetic', continueWithoutObservers: true, approval: { high: 'auto' } } });
    expect(created.ok(), await created.text()).toBe(true); sessionId = (await created.json()).sessionId;
    const route = `${api}/api/v1/sessions/${sessionId}`;
    const raised = await http.post(`${route}/actions`, { headers: { 'X-Actor': 'user:live-test' }, data: { requestId: 'raise-temperature', entityKey: incubator, action: 'setSetpoint', params: { temperatureC: 50 } } });
    expect(raised.status(), await raised.text()).toBe(202);
    await expect.poll(async () => (await (await http.get(`${api}/api/v1/alerts?sessionId=${sessionId}&active=true`)).json()).alerts.length).toBe(1);
    const [alert] = (await (await http.get(`${api}/api/v1/alerts?sessionId=${sessionId}&active=true`)).json()).alerts;
    await page.goto(web);
    await expect(page.getByTestId('critical-alert-banner')).toContainText('培养箱温度偏离设定值');
    await expect(page.getByTestId('live-alert-badge')).toHaveText('1');
    await page.getByRole('link', { name: '查看实况 →' }).click();
    await expect(page.getByRole('heading', { name: '实况 · 仿真设备' })).toBeVisible();
    await expect(page.getByRole('complementary', { name: '设备抽屉' })).toHaveAttribute('data-connection', 'connected');
    const label = page.getByTestId(`device-label-${incubator}`);
    await expect(label).toBeVisible();
    const hit = await label.evaluate(element => { const r = element.getBoundingClientRect(); return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.closest('button') === element; });
    expect(hit).toBe(true); await label.click();
    await expect(page.getByRole('complementary', { name: '设备抽屉' })).toContainText('incubator/v1');
    const canvas = await page.locator('canvas').boundingBox(), drawer = await page.getByRole('complementary', { name: '设备抽屉' }).boundingBox();
    expect(canvas!.width).toBeGreaterThan(400); expect(drawer!.x).toBeGreaterThanOrEqual(canvas!.x + canvas!.width);
    const rules = page.locator('.declared-rules');
    await expect(rules).toContainText('0.25 s'); await expect(rules).toContainText('tempDeviation');
    const alertRow = page.locator(`[data-alert-id="${alert.alertId}"]`);
    let ackRequests = 0; page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/ack')) ackRequests++; });
    await alertRow.getByRole('button', { name: '确认', exact: true }).click();
    await alertRow.getByLabel('确认备注').fill('先核对现场'); await alertRow.getByRole('button', { name: '取消', exact: true }).click();
    expect(ackRequests).toBe(0);
    await alertRow.getByRole('button', { name: '确认', exact: true }).click();
    await alertRow.getByLabel('确认备注').fill('已核对设定值，继续观察温度趋势');
    await alertRow.getByRole('button', { name: '提交确认', exact: true }).click();
    await expect(alertRow).toContainText('活动 · 已确认'); await expect(alertRow).toContainText('已核对设定值');
    const acknowledged = (await (await http.get(`${api}/api/v1/alerts?sessionId=${sessionId}`)).json()).alerts.find((value: { alertId: string }) => value.alertId === alert.alertId);
    expect(acknowledged.acknowledgement).toMatchObject({ actor: { kind: 'user', name: 'web' }, note: '已核对设定值，继续观察温度趋势' });
    // Acknowledgement does not claim recovery: the active badge remains.
    await expect(page.getByTestId('live-alert-badge')).toHaveText('1');
    const staleReading = page.getByTestId(`reading-${sensor}-humidityPercent`);
    await expect(staleReading).toContainText('过期');
    await expect(page.getByTestId(`trend-${sensor}-humidityPercent`)).toHaveClass(/is-stale/);
    await expect(page.getByTestId(`trend-${incubator}-temperatureC`)).toBeVisible();
    await expect(page.getByTestId(`trend-${incubator}-temperatureC`)).not.toContainText('实际跨度 60 分钟');
    await page.getByRole('button', { name: '读数', exact: true }).click(); await expect(page).toHaveURL(/readouts=false/);
    await expect(page.getByRole('button', { name: '读数', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await page.getByLabel('Color theme').selectOption('dark');
    await page.screenshot({ path: info.outputPath('live-dark-1440.png'), fullPage: true });
    await page.getByLabel('Color theme').selectOption('light');
    await page.screenshot({ path: info.outputPath('live-light-1440.png'), fullPage: true });
    // HTML load can precede lazy SPA initialization. Wait for this document's
    // completed read before checking its retained acknowledgement in the UI.
    let committed = false;
    const requestsAfterCommit = new WeakSet<Request>();
    const onCommit = (frame: Frame) => { if (frame === page.mainFrame()) committed = true; };
    const onRequest = (request: Request) => {
      if (!committed || request.frame() !== page.mainFrame() || request.method() !== 'GET') return;
      const url = new URL(request.url());
      if (url.origin === web && url.pathname === '/api/v1/alerts' && url.search === '?limit=100') requestsAfterCommit.add(request);
    };
    page.on('framenavigated', onCommit);
    page.on('request', onRequest);
    try {
      const [request] = await Promise.all([
        page.waitForEvent('requestfinished', { predicate: request => requestsAfterCommit.has(request) }),
        page.reload(),
      ]);
      const response = await request.response();
      expect(response?.status()).toBe(200);
      const refreshed = (await response!.json()).alerts.find((value: { alertId: string }) => value.alertId === alert.alertId);
      expect(refreshed).toMatchObject({ sessionId, acknowledgement: acknowledged.acknowledgement });
    } finally {
      page.off('framenavigated', onCommit);
      page.off('request', onRequest);
    }
    await expect(alertRow).toContainText('已核对设定值');
    const commands = page.locator('.live-command'); await expect(commands).toContainText(`lw history ${incubator} temperatureC --since 1h --every 1m`);
    await diagnosticResize(page, { width: 900, height: 960 });
    await expect.poll(() => diagnosticWidth(page)).toBeLessThanOrEqual(900);
    await page.screenshot({ path: info.outputPath('live-light-900.png'), fullPage: true });
    await page.goto(web); await expect(page.getByTestId('critical-alert-banner')).toContainText('已核对设定值');
    const evidence = JSON.stringify({ version, sessionId, alertId: alert.alertId, canvas, drawer, hit, ackRequests, acknowledgement: acknowledged.acknowledgement });
    await writeFile(info.outputPath('live-evidence.json'), evidence);
    await info.attach('live-evidence.json', { body: evidence, contentType: 'application/json' });
  } finally {
    if (sessionId) {
      const stopped = await http.delete(`${api}/api/v1/sessions/${sessionId}`).catch(() => undefined);
      if (stopped?.ok()) {
        const health = await http.get(`${api}/api/v1/health`);
        expect((await health.json()).sessions.find((value: { sessionId: string }) => value.sessionId === sessionId).state).toBe('STOPPED');
      }
    }
    await diagnosticBeforeBlank();
    await page.goto('about:blank').catch(() => {}); await http.dispose();
    for (const record of processes.reverse()) {
      if (record.child.exitCode === null && record.child.signalCode === null) record.child.kill('SIGTERM');
      let stopped = await Promise.race([record.exited.then(() => true), delay(3000).then(() => false)]);
      if (!stopped) { record.child.kill('SIGKILL'); stopped = await Promise.race([record.exited.then(() => true), delay(3000).then(() => false)]); }
      await writeFile(info.outputPath(`${record.label}.log`), record.output);
      await writeFile(info.outputPath(`${record.label}-receipt.json`), JSON.stringify({ pid: record.child.pid, source: resolve('../..'), api, web, stopped, exitCode: record.child.exitCode, signalCode: record.child.signalCode }));
      expect(stopped, `${record.label} owned process exited`).toBe(true);
    }
    expect(await closed(apiPort), 'Owned API listener gone').toBe(true);
    expect(await closed(webPort), 'Owned Vite listener gone').toBe(true);
  }
});
