import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { cleanupOwnedProcesses, finishCleanup, type OwnedProcess } from './owned-cleanup';

const key = '/Real/Incubator2', environment = '/Real/Environment1';
async function port() {
  return new Promise<number>((resolve, reject) => {
    const server = createServer(); server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address() as { port: number };
      server.close(error => error ? reject(error) : resolve(address.port));
    });
  });
}
test('real Live UI bootstraps plans, approves exact actions, retains alerts and replays without a simulation', async ({ page, playwright }, info) => {
  // Actual DeviceHost, programs, pipeline and writer; device peers are in-process,
  // socket-free fixtures. Actual MQTT wire composition has a separate manual check.
  const [apiPort, webPort] = await Promise.all([port(), port()]);
  const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const owned = resolve('../../tests/fixtures/owned_processes.py');
  const execute = promisify(execFile);
  const processes: OwnedProcess[] = [];
  const start = (label: string, executable: string, args: string[], cwd: string, env: NodeJS.ProcessEnv) => {
    const child = spawn(executable, args, { cwd, env, windowsHide: true });
    const record = { child, output: '', exited: new Promise<void>(resolve => child.once('exit', () => resolve())), label, error: undefined as Error | undefined,
      identity: execute(python, [owned, 'snapshot', String(child.pid)])
        .then(value => ({ identity: value.stdout }), error => ({ error })) };
    child.stdout?.on('data', data => { record.output += data; }); child.stderr?.on('data', data => { record.output += data; });
    child.once('error', error => { record.error = error; }); processes.push(record);
  };
  const env = { ...process.env, SPATIAL_REMOTE: '0', SPATIAL_ALLOWED_ORIGINS: web,
    LW_LIBRARY: info.outputPath('library'), SPATIAL_STATE_ROOT: info.outputPath('state'),
    SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings'), SPATIAL_API_URL: api, SPATIAL_LIVE_BINDINGS: '' };
  for (const directory of [env.LW_LIBRARY, env.SPATIAL_STATE_ROOT, env.SPATIAL_RECORDINGS_ROOT])
    expect(resolve(directory).startsWith(resolve(info.outputDir) + (process.platform === 'win32' ? '\\' : '/'))).toBe(true);
  const http = await playwright.request.newContext();
  const receipt = async () => (await (await http.get(`${api}/e2e/live`)).json());
  const forbidden: string[] = [];
  page.on('websocket', socket => forbidden.push(socket.url()));
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (/\/sessions\/[^/]+\/(manifest|stream|frames|entities|catalog)/.test(path)) forbidden.push(path);
  });
  let ready = false;
  const original: unknown[] = [];
  try {
    start('live-api', python, ['-m', 'uvicorn', 'live_operations_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('live-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []),
      '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const record of processes) if (record.error || record.child.exitCode !== null) throw record.error ?? new Error(record.output);
      try { return (await http.get(`${api}/api/v1/live`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    ready = true;
    const created = await http.post(`${api}/api/v1/plans`, { headers: { 'X-Actor': 'agent:live-browser' }, data: {
      title: 'Set real temperature before opening UI', target: 'live', steps: [
        { id: 1, do: { entity: key, action: 'setSetpoint', params: { temperatureC: 38 } } },
      ],
    } });
    expect(created.status(), await created.text()).toBe(201);
    const plan = await created.json();
    expect(plan.status).toBe('submitted'); expect(plan.approvalMode).toBe('human');
    const exact = { requestId: 'browser-exact-plan', entityKey: key, action: 'setSetpoint',
      params: { temperatureC: 38 }, planId: plan.id, stepId: 1, planRevision: plan.revision };
    const early = await http.post(`${api}/api/v1/live/actions`, { data: { ...exact, requestId: 'browser-before-approval' } });
    expect(early.status()).toBe(409); expect((await early.json()).error.code).toBe('plan_not_approved');
    expect((await receipt()).commands).toEqual([]);
    await page.goto(`${web}/live`);
    await expect(page.getByRole('heading', { name: '实况', exact: true })).toBeVisible();
    await expect(page.getByRole('complementary', { name: '设备抽屉' })).toHaveAttribute('data-connection', 'connected');
    await expect(page.getByTestId(`live-device-${key}`)).toContainText('37');
    await expect(page.getByLabel('选择操作计划')).toContainText(plan.title);
    await expect(page.getByTestId('plan-step-1')).toHaveAttribute('data-status', 'pending');
    await expect(page.locator('canvas')).toHaveCount(0);
    await page.goto(`${web}/approvals`);
    const approval = page.getByTestId(`approval-${plan.approvalId}`);
    await expect(approval).toContainText(plan.title);
    expect((await receipt()).commands).toEqual([]);
    await approval.getByRole('button', { name: '批准', exact: true }).click();
    await expect(approval).toContainText('已批准');
    expect((await receipt()).commands).toEqual([]);
    const invoked = await http.post(`${api}/api/v1/live/actions`, { data: exact });
    expect(invoked.status(), await invoked.text()).toBe(202);
    const invocation = await invoked.json();
    await expect.poll(async () => (await (await http.get(`${api}/api/v1/plans/${plan.id}`)).json()).status).toBe('completed');
    const duplicate = await http.post(`${api}/api/v1/live/actions`, { data: exact });
    expect((await duplicate.json()).invocationId).toBe(invocation.invocationId);
    expect((await receipt()).commands).toHaveLength(1);
    await page.goto(`${web}/plans/${plan.id}`);
    await expect(page.locator('.plan-context')).toContainText('实况设备计划');
    await expect(page.getByTestId('plan-step-1')).toHaveAttribute('data-status', 'done');
    await expect(page.getByTestId('plan-preview-figure')).toHaveCount(0);
    await page.getByRole('link', { name: '打开实况 ↗' }).click();
    await expect(page.getByTestId(`live-device-${key}`)).toContainText('38');
    await http.post(`${api}/e2e/live/fault`, { data: { mode: 'heat' } });
    await expect.poll(async () => (await (await http.get(`${api}/api/v1/alerts?active=true`)).json()).alerts.length).toBe(1);
    const [alert] = (await (await http.get(`${api}/api/v1/alerts?active=true`)).json()).alerts;
    const row = page.locator(`[data-alert-id="${alert.alertId}"]`);
    await expect(row).toContainText('培养箱温度偏离设定值');
    let ackRequests = 0;
    page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/ack')) ackRequests++; });
    await row.getByRole('button', { name: '确认', exact: true }).click();
    await row.getByLabel('确认备注').fill('取消不会确认');
    await row.getByRole('button', { name: '取消', exact: true }).click();
    expect(ackRequests).toBe(0);
    await row.getByRole('button', { name: '确认', exact: true }).click();
    await row.getByLabel('确认备注').fill('已核对真实设备设定值');
    await row.getByRole('button', { name: '提交确认', exact: true }).click();
    await expect(row).toContainText('活动 · 已确认');
    await expect(row).toContainText('已核对真实设备设定值');
    const inbox = await (await http.get(`${api}/api/v1/inbox?kinds=alert`)).json();
    expect(inbox.alerts[0].alertId).toBe(alert.alertId);
    await http.post(`${api}/e2e/live/fault`, { data: { mode: 'normal' } });
    await expect.poll(async () => (await (await http.get(`${api}/api/v1/inbox?after=${encodeURIComponent(inbox.cursor)}&kinds=alert`)).json())
      .messages.map((message: { event: string }) => message.event)).toContain('cleared');
    await expect(row).toContainText('已恢复');
    const history = await http.get(`${api}/api/v1/history`, { params: { key, property: 'temperatureC', since: '1h' } });
    expect(history.ok(), await history.text()).toBe(true);
    expect((await history.json()).map((point: [number, number]) => point[1])).toEqual(expect.arrayContaining([37, 38, 42]));
    await expect(page.getByTestId(`trend-${key}-temperatureC`)).toBeVisible();
    await expect(page.getByTestId(`trend-${key}-temperatureC`)).not.toContainText('实际跨度 60 分钟');
    await http.post(`${api}/e2e/live/fault`, { data: { mode: 'quiet' } });
    await expect(page.getByTestId(`reading-${key}-temperatureC`)).toContainText('过期');
    await expect(page.getByTestId(`reading-${environment}-temperatureC`)).not.toContainText('过期');
    const offline = await http.post(`${api}/api/v1/live/actions`, { data: { ...exact, requestId: 'offline', planId: undefined, stepId: undefined, planRevision: undefined } });
    expect(offline.status()).toBe(503); expect((await offline.json()).error.code).toBe('device_unavailable');
    expect((await receipt()).commands).toHaveLength(1);
    await page.getByLabel('Color theme').selectOption('dark');
    await page.screenshot({ path: info.outputPath('real-live-dark-1440.png'), fullPage: true });
    await page.getByLabel('Color theme').selectOption('light');
    await page.setViewportSize({ width: 900, height: 960 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(900);
    await page.screenshot({ path: info.outputPath('real-live-light-900.png'), fullPage: true });
    await page.getByRole('link', { name: '回放实况录制 →' }).click();
    await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
    await page.getByLabel('Replay position').press('End');
    await expect(page.getByTestId(`invocation-${invocation.invocationId}`)).toContainText('succeeded');
    const replayDevice = page.getByTestId(`replay-device-${key}`);
    await expect(replayDevice.getByTestId('property-temperatureC').locator('output')).toHaveText('38');
    await replayDevice.getByTestId('action-setSetpoint').locator('summary').click();
    await expect(replayDevice.getByRole('button', { name: 'Run setSetpoint', exact: true })).toBeDisabled();
    expect(forbidden).toEqual([]);
    expect(await receipt()).toMatchObject({ sourceCalls: 0, sessions: 0 });
    await writeFile(info.outputPath('live-composition-evidence.json'), JSON.stringify({ planId: plan.id,
      invocationId: invocation.invocationId, alertId: alert.alertId, ackRequests, forbidden, ...(await receipt()) }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script: owned, original,
      outputPath: name => info.outputPath(name), context: { source: resolve('../..'), api, web },
      ports: [apiPort, webPort], prepare: [
        { label: 'live:close', run: async () => {
          if (!ready) return;
          const shutdown = await http.post(`${api}/e2e/live/close`);
          expect(shutdown.ok(), 'Owned Live Host closed').toBe(true);
          expect(await shutdown.json()).toMatchObject({ sourceCalls: 0, sessions: 0, peerTasksDone: true });
        } },
        { label: 'page:close', run: () => page.goto('about:blank') },
        { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
