import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { cleanupOwnedProcesses, finishCleanup, type OwnedProcess } from './owned-cleanup';

async function freePort() {
  return new Promise<number>((resolve, reject) => {
    const server = createServer(); server.once('error', reject);
    server.listen(0, '127.0.0.1', () => { const port = (server.address() as { port: number }).port;
      server.close(error => error ? reject(error) : resolve(port)); });
  });
}

test('cost ranges, filters, actual fake-node counters and anomaly acknowledgement share HTTP and sidebar state', async ({ page, playwright }, info) => {
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
  const env = { ...process.env, SPATIAL_REMOTE: '0', SPATIAL_ALLOWED_ORIGINS: web, SPATIAL_API_URL: api,
    SPATIAL_NODE_TRAINER: '[]', SPATIAL_LIVE_BINDINGS: '', LW_LIBRARY: info.outputPath('library'),
    SPATIAL_STATE_ROOT: info.outputPath('state'), SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings') };
  const http = await playwright.request.newContext();
  const headers = { 'X-Node-Token': 'training-resources-browser-fixture-token', 'X-Node-Instance': 'training-resources-browser-node' };
  const forbidden: string[] = [], errors: string[] = [];
  let viewingCost = false;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if ((viewingCost || request.method() !== 'GET') && /\/api\/v1\/(sessions|recordings)(\/|$)/.test(new URL(request.url()).pathname))
      forbidden.push(request.url());
  });
  let running: { jobId: string; leaseId: string } | undefined, node: object | undefined;
  const heartbeat = async (reports: object[]) => {
    const response = await http.post(`${api}/api/v1/nodes/local/heartbeat`, { headers, data: { ...node, reports } });
    expect(response.ok(), await response.text()).toBe(true);
  };
  try {
    start('cost-api', python, ['-m', 'uvicorn', 'cost_gateway:app', '--app-dir', 'tests/fixtures', '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('cost-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'), ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []),
      '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20000 }).toBe(true);
    const roots = await (await http.get(`${api}/e2e/storage-roots`)).json();
    for (const path of Object.values(roots) as string[]) expect(resolve(path).startsWith(resolve(info.outputDir))).toBe(true);
    const now = Date.now() / 1000;
    const { stdout } = await execute(python, [resolve('../../tests/fixtures/training_task.py'), `cost-${randomUUID()}`], { encoding: 'buffer' });
    const uploaded = await http.post(`${api}/api/v1/tasks`, { headers: { 'Content-Type': 'application/x-tar' }, data: stdout });
    expect(uploaded.ok(), await uploaded.text()).toBe(true); const task = await uploaded.json();
    const admit = async (goal: string, agent: string, at: number) => {
      expect((await http.post(`${api}/e2e/cost-time`, { data: { at } })).ok()).toBe(true);
      const submitted = await http.post(`${api}/api/v1/plans`, { data: {
        kind: 'training', title: goal, goal, task: task.id,
        algorithm: { library: 'rsl_rl', name: 'PPO', version: '5.4.1' }, model: { actor: [32], critic: [32] },
        hyperparameters: { num_envs: 4, max_iterations: 10 }, jobs: { seeds: [42] }, budget: { gpuHours: 10 },
        evaluation: { scene: 'robot-and-tubes@2', episodes: 100, threshold: 85 }, rationale: 'GPU-free accounting browser fixture.',
        hypotheses: [{ id: 'H41.1', cause: '用量缺少可见归属', change: '记录已接受的测试计数', predict: { metric: 'gpu.seconds', to: '>= 0' } }],
        notebookRead: [], interview: '测试问题：运行真实训练吗？回答：不运行；仅假节点计数。',
      } });
      expect(submitted.ok(), await submitted.text()).toBe(true); const plan = await submitted.json();
      expect((await http.post(`${api}/api/v1/approvals/${plan.approvalId}/decision`, { data: { decision: 'approve' } })).ok()).toBe(true);
      node = await (await http.get(`${api}/e2e/training-profile/${plan.id}`)).json(); await heartbeat([]);
      const created = await http.post(`${api}/api/v1/jobs`, { headers: { 'X-Actor': `agent:${agent}` }, data: { planId: plan.id, seed: 42 } });
      expect(created.status(), await created.text()).toBe(202); const job = await created.json(); expect(job.leaseId).toBeTruthy();
      return { plan, job };
    };
    const old = await admit('goal/cost-history', 'cost-history', now - 2 * 86400);
    await heartbeat([{ jobId: old.job.jobId, leaseId: old.job.leaseId, status: 'failed', elapsedSeconds: 3600, memoryUsedMb: 512, reason: 'fixture_failure' }]);
    expect((await http.put(`${api}/api/v1/prices`, { headers: { 'X-Actor': 'user:cost-fixture' }, data: { revision: 0, gpus: [{ nodeId: 'local', usdPerHour: 2 }] } })).ok()).toBe(true);
    const current = await admit('goal/cost-current', 'cost-current', now); running = current.job;
    await heartbeat([{ jobId: running!.jobId, leaseId: running!.leaseId, status: 'running', elapsedSeconds: 1800, memoryUsedMb: 1024 }]);
    expect((await http.post(`${api}/api/v1/models`, { data: { provider: 'cost-fixture', modelId: 'unconnected' } })).ok()).toBe(true);

    await page.goto(web);
    await expect(page.getByTestId('cost-unread-badge')).toBeVisible();
    viewingCost = true;
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Cost', exact: true }).click();
    await expect(page.getByRole('heading', { name: '成本与统计', exact: true })).toBeVisible();
    await expect(page.getByTestId('cost-selected-gpu')).toContainText('1.5');
    await expect(page.getByLabel('当前范围成本摘要')).toContainText('未定价 3,600 GPU 秒');
    await expect(page.getByLabel('当前范围成本摘要')).toContainText('已定价部分 $1.00');
    await expect(page.getByLabel('今日 · UTC 成本摘要')).toContainText('0.5');
    await expect(page.getByLabel('累计 成本摘要')).toContainText('1.5');
    await expect(page.getByRole('img', { name: /独立成功率带/ })).toContainText('暂无已上报请求');
    await expect(page.getByRole('img', { name: /独立缓存命中率带/ })).toContainText('暂无已上报 Token');
    await expect(page.getByRole('table', { name: '按计划分摊', exact: true })).toContainText(`${current.plan.id}@1`);
    await page.getByRole('button', { name: '最近一小时', exact: true }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('range')).toBe('1h');
    await expect(page.getByTestId('cost-selected-gpu')).toContainText('0.5');
    await expect(page.getByLabel('当前范围成本摘要')).not.toContainText('未定价');
    await page.getByLabel('成本Agent', { exact: true }).selectOption('cost-history');
    await expect(page.getByTestId('cost-selected-gpu')).toHaveText('0 GPU 小时');
    await page.getByRole('button', { name: '近 7 天', exact: true }).click();
    await expect(page.getByTestId('cost-selected-gpu')).toHaveText('1 GPU 小时');
    await page.getByLabel('成本Agent', { exact: true }).selectOption('');
    await page.getByLabel('成本模型', { exact: true }).selectOption('cost-fixture/unconnected');
    await expect(page.getByTestId('cost-selected-gpu')).toHaveText('0 GPU 小时');
    await expect(page.getByLabel('累计 成本摘要')).toContainText('未接入');
    await page.getByLabel('成本模型', { exact: true }).selectOption('');
    await page.getByLabel('成本计划', { exact: true }).selectOption(`${current.plan.id}@1`);
    await expect(page.getByTestId('cost-selected-gpu')).toContainText('0.5');
    await page.reload(); await expect(page.getByLabel('成本计划', { exact: true })).toHaveValue(`${current.plan.id}@1`);
    await page.getByLabel('成本计划', { exact: true }).selectOption('');
    const gpuDetails = page.locator('.cost-chart-data').filter({ hasText: '查看 GPU 数据表' });
    await gpuDetails.locator('summary').focus(); await gpuDetails.locator('summary').press('Enter');
    await expect(gpuDetails.getByRole('table', { name: 'GPU 用量数据表' })).toContainText('3,600');
    await expect(gpuDetails.getByRole('table', { name: 'GPU 用量数据表' })).toContainText('1,800');
    await page.getByRole('button', { name: '自定义', exact: true }).click();
    await page.getByLabel('成本开始时间').fill(new Date((now - 3 * 86400) * 1000).toISOString().slice(0, 16));
    await page.getByLabel('成本结束时间').fill(new Date((now + 86400) * 1000).toISOString().slice(0, 16));
    await expect(page.getByTestId('cost-selected-gpu')).toContainText('1.5');
    await expect.poll(() => new URL(page.url()).searchParams.get('range')).toBe('custom');
    await page.getByRole('button', { name: '全部标为已读', exact: true }).click();
    await expect(page.getByTestId('cost-unread-badge')).toHaveCount(0);
    await expect(page.locator('.cost-anomalies')).toContainText('已读');
    expect((await (await http.get(`${api}/api/v1/cost/anomalies`)).json()).unreadCount).toBe(0);
    await page.goto(`${web}/cost`);
    await expect.poll(() => new URL(page.url()).searchParams.get('range')).toBe('custom');
    await expect(page.getByTestId('cost-selected-gpu')).toContainText('1.5');
    await page.screenshot({ path: info.outputPath('cost-light.png'), fullPage: true });
    await page.getByLabel('Color theme').selectOption('dark'); await page.setViewportSize({ width: 900, height: 1000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath('cost-dark-900.png'), fullPage: true });
    await page.setViewportSize({ width: 400, height: 900 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]); expect(forbidden).toEqual([]);
    const receipt = await (await http.get(`${api}/api/v1/cost/summary?range=all`)).json();
    expect(receipt.totals.gpuSeconds).toBe(5400); expect(receipt.totals.gpuUsd).toBeNull(); expect(receipt.totals.knownGpuUsd).toBe(1);
    await writeFile(info.outputPath('cost-receipt.json'), JSON.stringify({ receipt, old: old.job.jobId, current: running!.jobId, errors, forbidden,
      scope: 'Authenticated fake-node HTTP counters and actual browser; no real GPU job or paid model usage' }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script: owned, original,
      outputPath: name => info.outputPath(name), context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'fake-node:release-lease', run: async () => {
          if (running) { expect((await http.post(`${api}/api/v1/jobs/${running.jobId}/cancel`)).ok()).toBe(true);
            await heartbeat([{ jobId: running.jobId, leaseId: running.leaseId, status: 'cancelled', elapsedSeconds: 1801 }]); }
        } }, { label: 'page:close', run: () => page.goto('about:blank') }, { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
