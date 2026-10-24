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

test('Agent training shows actual ranges rejection restore private human promotion and contamination', async ({ page, playwright }, info) => {
  test.setTimeout(150_000);
  const [apiPort, webPort] = await Promise.all([freePort(), freePort()]);
  const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const script = resolve('../../tests/fixtures/owned_processes.py'), execute = promisify(execFile);
  const processes: OwnedProcess[] = [], original: unknown[] = [], errors: string[] = [], sockets: string[] = [];
  const start = (label: string, executable: string, args: string[], cwd: string, env: NodeJS.ProcessEnv) => {
    const child = spawn(executable, args, { cwd, env, windowsHide: true });
    const record: OwnedProcess = { child, label, output: '', exited: new Promise<void>(resolve => child.once('exit', () => resolve())),
      identity: execute(python, [script, 'snapshot', String(child.pid)]).then(value => ({ identity: value.stdout }), error => ({ error })) };
    child.stdout?.on('data', chunk => { record.output += chunk; }); child.stderr?.on('data', chunk => { record.output += chunk; });
    child.once('error', error => { record.error = error; }); processes.push(record);
  };
  const env = { ...process.env, SPATIAL_REMOTE: '0', SPATIAL_ALLOWED_ORIGINS: web, SPATIAL_API_URL: api,
    SPATIAL_NODE_TRAINER: '[]', SPATIAL_LIVE_BINDINGS: '', LW_LIBRARY: info.outputPath('library'),
    SPATIAL_STATE_ROOT: info.outputPath('state'), SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings') };
  const http = await playwright.request.newContext();
  page.on('pageerror', error => errors.push(error.message)); page.on('websocket', socket => sockets.push(socket.url()));
  try {
    start('agent-rsi-api', python, ['-m', 'uvicorn', 'agent_rsi_ui_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('agent-rsi-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const owned of processes) if (owned.error || owned.child.exitCode !== null) throw owned.error ?? new Error(owned.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    const seeded = await http.post(`${api}/e2e/agent-rounds`, { timeout: 90_000 });
    expect(seeded.ok(), await seeded.text()).toBe(true);
    const seed = await seeded.json();
    await page.goto(`${web}/train?layer=agent&agent=${encodeURIComponent(seed.agentId)}&goal=${encodeURIComponent(seed.goalId)}`);
    const panel = page.getByTestId('agent-training-page');
    await expect(page.getByLabel('训练 Agent', { exact: true })).toHaveValue(seed.agentId);
    await expect(page.getByLabel('Agent 训练 Goal', { exact: true })).toHaveValue(seed.goalId);
    await expect(panel.getByRole('img', { name: 'Agent 每版本均分与最低最高分，纵轴0到100' })).toBeVisible();
    await expect(panel.getByRole('table', { name: 'Agent 实验记录' })).toContainText('73.3 / 100');
    await expect(panel.getByRole('table', { name: 'Agent 实验记录' })).toContainText('40–90');
    await expect(panel.getByRole('table', { name: 'Agent 实验记录' })).toContainText('55.56%');
    const approval = panel.getByTestId(`approval-${seed.approvalId}`);
    await expect(approval.getByRole('alert')).toContainText('候选的 held-out 得分低于 Reference');
    await page.getByLabel('Color theme').selectOption('dark');
    await expect(approval.locator('.below-reference')).toHaveCSS('color', 'rgb(242, 118, 106)');
    await expect(approval.locator('.below-reference')).toHaveCSS('border-left-color', 'rgb(242, 118, 106)');
    await page.getByLabel('Color theme').selectOption('light');
    await expect(approval.locator('.below-reference')).toHaveCSS('color', 'rgb(198, 61, 47)');
    await expect(approval.getByRole('table')).toContainText('100.0 / 100');
    await expect(approval.getByRole('table')).toContainText('80.0 / 100');
    expect((await (await http.get(`${api}/api/v1/agents/lab-operator`)).json()).referenceVersion).toBe(1);
    expect((await http.post(`${api}/e2e/promotion-publication-fault`)).ok()).toBe(true);
    await approval.getByRole('textbox').fill('已检查较低的 held-out 结果，批准此受控工程晋升。');
    await approval.getByRole('button', { name: '批准', exact: true }).click();
    await expect(approval.getByRole('button', { name: '重试 Reference 恢复', exact: true })).toBeVisible();
    expect((await (await http.get(`${api}/api/v1/plans/${seed.planId}`)).json()).result.referenceAdvanced).toBe(false);
    await approval.getByRole('button', { name: '重试 Reference 恢复', exact: true }).click();
    await expect.poll(async () => (await (await http.get(`${api}/api/v1/plans/${seed.planId}`)).json()).result.referenceAdvanced).toBe(true);
    await expect.poll(async () => (await (await http.get(`${api}/api/v1/agents/lab-operator`)).json()).referenceVersion).toBe(2);
    const rejected = await http.post(`${api}/e2e/agent-rejected`, { timeout: 30_000 });
    expect(rejected.ok(), await rejected.text()).toBe(true);
    const rejectedRound = await rejected.json();
    await page.reload();
    await expect(panel.locator(`.agent-score-point[data-version="${rejectedRound.version}"]`)).toHaveClass(/is-rejected/);
    await expect(panel.locator(`.agent-score-point[data-version="${rejectedRound.version}"] circle`)).toHaveAttribute('data-accepted', 'false');
    await expect(panel.getByRole('table', { name: 'Harness 版本历史' })).toContainText('已拒绝');
    await expect(panel.getByRole('table', { name: 'Agent 运行记录' }).getByRole('row')).toHaveCount(4);
    await panel.getByRole('button', { name: '人工改判 H1', exact: true }).first().click();
    await page.getByLabel('Agent 人工假设判定').selectOption('inconclusive');
    await page.getByLabel('Agent 改判说明').fill('保留原始评分；对本轮假设采用人工证据不足判断。');
    await page.getByRole('button', { name: '记录人工判定', exact: true }).click();
    await expect(panel.getByRole('table', { name: 'Agent 实验记录' })).toContainText('人工证据不足判断');
    await page.route('**/api/v1/agents/lab-operator/rsi', route => route.abort());
    await page.getByRole('button', { name: '刷新 Agent 记录', exact: true }).click();
    await expect(panel.getByRole('alert')).toContainText('保留最近一次结果');
    await expect(panel.getByRole('table', { name: 'Harness 版本历史' })).toBeVisible();
    await page.unroute('**/api/v1/agents/lab-operator/rsi');
    await page.getByRole('button', { name: '重试', exact: true }).click();
    await expect(panel.getByRole('alert')).toHaveCount(0);
    const contaminated = await http.post(`${api}/e2e/agent-contaminated`, { timeout: 30_000 });
    expect(contaminated.ok(), await contaminated.text()).toBe(true);
    await page.reload();
    await expect(panel).toContainText('污染停止 · 已恢复 Reference 文件');
    await expect(panel.getByRole('table', { name: 'Agent 实验记录' }).getByRole('row')).toHaveCount(4);
    await expect(panel.locator('.agent-score-point')).toHaveCount(2);
    const late = await http.post(`${api}/e2e/agent-late-contaminated`, { timeout: 30_000 });
    expect(late.ok(), await late.text()).toBe(true);
    const lateRound = await late.json();
    await page.reload();
    const lateRow = panel.getByRole('table', { name: 'Agent 实验记录' }).getByRole('row').filter({ hasText: 'Controlled late-accepted round' });
    await expect(lateRow).toContainText('污染停止');
    await expect(lateRow).toContainText('历史自动：训练接受');
    await expect(panel.locator(`.agent-score-point[data-version="${lateRound.version}"]`)).toHaveCount(0);
    await expect(panel.locator('.agent-score-point')).toHaveCount(2);
    expect(await panel.textContent()).not.toContain('CONTROLLED-PRIVATE-RSI-CANARY-45');
    const unavailable = await http.post(`${api}/e2e/agent-reference-invalid`);
    expect(unavailable.ok(), await unavailable.text()).toBe(true);
    await page.reload();
    await expect(panel.getByTestId('agent-current-reference')).toContainText('当前条件待重新评估');
    await expect(panel.getByTestId('agent-current-reference').locator('strong')).toHaveText('未知');
    await expect(panel.locator('.agent-reference')).toHaveCount(0);
    await page.getByLabel('Color theme').selectOption('dark');
    await panel.screenshot({ path: info.outputPath('agent-rsi-dark.png') });
    await page.setViewportSize({ width: 1000, height: 1000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await panel.screenshot({ path: info.outputPath('agent-rsi-1000.png') });
    await page.getByRole('link', { name: '具身层', exact: true }).click();
    await expect(page.getByTestId('training-page')).toBeVisible();
    await expect(page.getByTestId('agent-training-page')).toHaveCount(0);
    await page.getByRole('link', { name: 'Agent 层', exact: true }).click();
    await expect(page.getByTestId('agent-training-page')).toBeVisible();
    expect(errors).toEqual([]); expect(sockets).toEqual([]);
    expect((await (await http.get(`${api}/api/v1/health`)).json()).sessions.every((value: { state: string }) => value.state === 'STOPPED')).toBe(true);
    await writeFile(info.outputPath('agent-rsi-receipt.json'), JSON.stringify({ seed, rejectedRound, errors, sockets,
      source: 'Actual Synthetic Sessions, production identity producer, full train/private repeats, human file promotion and contamination; no model or GPU' }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script, original, outputPath: name => info.outputPath(name),
      context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'page:close', run: () => page.goto('about:blank') }, { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
