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

test('training Goal shows two rounds and independent decisions, budget warning and durable human audit', async ({ page, playwright }, info) => {
  const [apiPort, webPort] = await Promise.all([freePort(), freePort()]);
  const api = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const script = resolve('../../tests/fixtures/owned_processes.py'), execute = promisify(execFile);
  const processes: OwnedProcess[] = [], original: unknown[] = [];
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
  const errors: string[] = [], forbidden: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('websocket', socket => forbidden.push(socket.url()));
  page.on('request', request => { if (/\/api\/v1\/(sessions|recordings)(\/|$)/.test(new URL(request.url()).pathname)) forbidden.push(request.url()); });
  try {
    start('notebook-api', python, ['-m', 'uvicorn', 'notebooks_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('notebook-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    const seed = await http.post(`${api}/e2e/notebook-rounds`);
    expect(seed.ok(), await seed.text()).toBe(true);
    const data = await seed.json();
    await page.goto(`${web}/train?goal=${encodeURIComponent(data.goalId)}`);
    const panel = page.getByTestId('goal-notebook-panel');
    await expect(page.getByLabel('选择改进目标')).toHaveValue(data.goalId);
    await expect(panel.getByRole('img', { name: /各轮成功率和95%置信区间/ })).toBeVisible();
    const table = panel.getByRole('table', { name: '实验记录表' });
    await expect(table.getByRole('row')).toHaveCount(3);
    const reference = table.locator(`tr[data-entry-id="${data.entries[0]}"]`), candidate = table.locator(`tr[data-entry-id="${data.entries[1]}"]`);
    await expect(reference).toContainText('0/2'); await expect(reference).toContainText('预测被反驳');
    await expect(candidate).toContainText('2/2'); await expect(candidate).toContainText('达成预测');
    await expect(candidate).toContainText('平台记录接受'); await expect(candidate).toContainText('未申请部署');
    await expect(candidate).toContainText('100.0%');
    await expect(panel.locator('.rsi-target-line')).toHaveCount(1);
    await expect(panel.locator('.rsi-reference-line')).toHaveCount(1);
    await expect(panel.locator('.rsi-series')).toHaveCount(1);
    await expect(panel.getByRole('meter', { name: '评估尝试有效容量' })).toHaveAttribute('value', '4');
    const approval = page.getByTestId(`approval-${data.pendingApproval}`);
    await expect(approval).toContainText('闭环预算已用尽');
    expect((await (await http.get(`${api}/api/v1/plans/${data.plans[1]}`)).json()).status).toBe('approved');
    await reference.getByRole('button', { name: '人工改判 H1', exact: true }).click();
    await page.getByLabel('人工假设判定').selectOption('inconclusive');
    await page.getByLabel('改判说明').fill('人工审阅：保留预测原始结果，当前证据不足。');
    await page.getByRole('button', { name: '记录人工判定', exact: true }).click();
    await expect(reference).toContainText('H1 · 证据不足');
    await expect(reference).toContainText('自动：预测被反驳');
    const notebook = await (await http.get(`${api}/api/v1/notebooks/${data.goalId}`)).json();
    expect(notebook.entries[0].hypotheses[0].overrides).toHaveLength(1);
    expect(notebook.entries[0].reportedDecision).toBe('baseline');
    expect(notebook.entries[1].reportedDecision).toBe('accepted');
    expect(JSON.stringify(notebook)).not.toContain('PRIVATE-HELDOUT-NOTEBOOK-DO-NOT-PUBLISH-729618');
    await page.reload(); await expect(reference).toContainText('H1 · 证据不足');
    await page.getByLabel('Color theme').selectOption('dark');
    await panel.screenshot({ path: info.outputPath('rsi-notebook-dark.png') });
    await page.setViewportSize({ width: 900, height: 1000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await panel.screenshot({ path: info.outputPath('rsi-notebook-900.png') });
    expect((await (await http.get(`${api}/api/v1/health`)).json()).sessionCount).toBe(0);
    expect(forbidden).toEqual([]); expect(errors).toEqual([]);
    await writeFile(info.outputPath('notebook-receipt.json'), JSON.stringify({ data, notebook, errors, forbidden,
      scope: 'Actual HTTP/approval/evaluator-adapter/store/UI; controlled receipts, no physics/training/GPU' }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script, original, outputPath: name => info.outputPath(name),
      context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'page:close', run: () => page.goto('about:blank') }, { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
