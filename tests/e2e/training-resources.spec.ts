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
    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as { port: number }).port;
      server.close(error => error ? reject(error) : resolve(port));
    });
  });
}

test('training overview and compute resources use real HTTP jobs, approvals and fake-node telemetry', async ({ page, playwright }, info) => {
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
  const env = { ...process.env, SPATIAL_REMOTE: '0', SPATIAL_ALLOWED_ORIGINS: web,
    SPATIAL_API_URL: api, SPATIAL_NODE_TRAINER: '[]', SPATIAL_LIVE_BINDINGS: '',
    LW_LIBRARY: info.outputPath('library'), SPATIAL_STATE_ROOT: info.outputPath('state'),
    SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings') };
  const http = await playwright.request.newContext();
  const headers = { 'X-Node-Token': 'training-resources-browser-fixture-token', 'X-Node-Instance': 'training-resources-browser-node' };
  let node: Record<string, unknown> | undefined, job: { jobId: string; leaseId: string } | undefined, queued: { jobId: string } | undefined;
  const forbidden: string[] = [], errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.method() !== 'GET' && /\/api\/v1\/(sessions|recordings)(\/|$)/.test(new URL(request.url()).pathname))
      forbidden.push(`${request.method()} ${request.url()}`);
  });
  const heartbeat = async (utilization: number | null, observedAt: number, reports: object[] = []) => {
    const response = await http.post(`${api}/api/v1/nodes/local/heartbeat`, { headers, data: { ...node,
      gpus: [{ id: 'GPU-training-resources-fixture', name: 'Browser fixture GPU', memoryTotalMb: 8192,
        utilization, memoryUsedMb: utilization == null ? null : 0, observedAt,
        unavailableReason: utilization == null ? 'Driver sample is unavailable' : null }], reports } });
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  try {
    start('training-resources-api', python, ['-m', 'uvicorn', 'training_resources_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('training-resources-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    const roots = await (await http.get(`${api}/e2e/storage-roots`)).json();
    for (const path of Object.values(roots) as string[]) expect(resolve(path).startsWith(resolve(info.outputDir))).toBe(true);
    const { stdout } = await execute(python, [resolve('../../tests/fixtures/training_task.py'), `resources-${randomUUID()}`], { encoding: 'buffer' });
    const upload = await http.post(`${api}/api/v1/tasks`, { headers: { 'Content-Type': 'application/x-tar' }, data: stdout });
    expect(upload.ok(), await upload.text()).toBe(true);
    const task = await upload.json();
    const document = { kind: 'training', title: '真实 HTTP 训练资源验收', goal: '验证作业与节点可见性', task: task.id,
      algorithm: { library: 'rsl_rl', name: 'PPO', version: '5.4.1' }, model: { actor: [32, 32], critic: [32, 32] },
      hyperparameters: { num_envs: 4, max_iterations: 10 }, jobs: { seeds: [42, 43] }, budget: { gpuHours: 1 },
      evaluation: { scene: 'robot-and-tubes@2', episodes: 100, threshold: 85 }, rationale: 'GPU-free browser fixture only.',
      hypotheses: [{ id: 'H33.1', cause: '节点状态不可见', change: '显示真实心跳指标', predict: { metric: 'visible.nodes', to: '>= 1' } }],
      notebookRead: [], interview: '| 问题 | Agent 推荐 | 人的回答 |\n| --- | --- | --- |\n| 使用什么数据？ | 只使用测试假节点。 | 是，不启动训练。 |' };
    const submitted = await http.post(`${api}/api/v1/plans`, { data: document });
    expect(submitted.ok(), await submitted.text()).toBe(true);
    const plan = await submitted.json();
    await page.goto(`${web}/train`);
    await expect(page.getByRole('heading', { name: '训练', exact: true })).toBeVisible();
    await expect(page.getByRole('article', { name: '未接入计算节点' })).toHaveClass(/is-unconnected/);
    const approval = page.getByTestId(`approval-${plan.approvalId}`);
    await expect(approval).toContainText('H33.1 · 节点状态不可见');
    await expect(approval.getByRole('table', { name: '训练访谈问答' })).toContainText('是，不启动训练。');
    await approval.getByLabel('备注').fill('仅批准隔离浏览器测试数据');
    await approval.getByRole('button', { name: '批准', exact: true }).click();
    await expect(approval).toHaveCount(0);
    expect((await (await http.get(`${api}/api/v1/approvals/${plan.approvalId}`)).json()).status).toBe('approved');
    node = await (await http.get(`${api}/e2e/training-profile/${plan.id}`)).json();
    const now = Date.now() / 1000;
    await heartbeat(0, now - 50); await heartbeat(65, now - 30); await heartbeat(null, now - 10);
    const first = await http.post(`${api}/api/v1/jobs`, { data: { planId: plan.id, seed: 42 } });
    expect(first.status(), await first.text()).toBe(202); job = await first.json();
    const second = await http.post(`${api}/api/v1/jobs`, { data: { planId: plan.id, seed: 43 } });
    expect(second.status(), await second.text()).toBe(202); queued = await second.json();
    await heartbeat(null, now, [{ jobId: job!.jobId, leaseId: job!.leaseId, status: 'running', iteration: 2,
      elapsedSeconds: 12, meanReward: 0, trainSuccess: null, memoryUsedMb: 512 }]);
    await page.getByRole('button', { name: '刷新作业与节点' }).click();
    const rows = page.getByRole('table', { name: '训练作业表' });
    const running = rows.getByRole('row').filter({ hasText: job!.jobId });
    await expect(running).toContainText('运行中'); await expect(running).toContainText('2 / 10');
    await expect(running.locator('td').nth(5)).toHaveText('0');
    await expect(running.locator('td').nth(6)).toHaveText('未知');
    await page.getByLabel('筛选作业状态').selectOption('queued');
    await expect.poll(() => new URL(page.url()).searchParams.get('status')).toBe('queued');
    await expect(rows.getByRole('row').filter({ hasText: job!.jobId })).toHaveCount(0);
    await expect(rows.getByRole('row').filter({ hasText: queued!.jobId })).toContainText('GPU 租约被占用');
    await page.getByLabel('筛选作业状态').selectOption('');
    await page.getByLabel('搜索训练作业').fill(queued!.jobId);
    await expect.poll(() => new URL(page.url()).searchParams.get('q')).toBe(queued!.jobId);
    await expect(rows.getByRole('row').filter({ hasText: job!.jobId })).toHaveCount(0);
    await page.getByLabel('搜索训练作业').fill('');
    for (const [label, path] of [['素材库', '/assets'], ['场景版本', '/scenes'], ['计划与审批', '/plans'], ['评估中心', '/eval']]) {
      await page.getByRole('navigation', { name: '具身层阶段' }).getByRole('link', { name: new RegExp(label) }).click();
      await expect(page).toHaveURL(new RegExp(path));
      await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Training', exact: true }).click();
    }
    await page.getByRole('navigation', { name: '具身层阶段' }).getByRole('link', { name: /策略版本/ }).click();
    await expect(page).toHaveURL(/#policies$/);
    await expect(page.getByRole('heading', { name: '策略与改进记录尚未接入' })).toBeVisible();
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Resources', exact: true }).click();
    const panel = page.getByTestId('compute-node-local');
    await expect(panel).toContainText('使用率 未知'); await expect(panel).toContainText('Driver sample is unavailable');
    await expect(panel.getByRole('img', { name: /过去一小时 GPU 使用率/ })).toBeVisible();
    await expect(panel).toContainText('未保留或未采样时留空');
    await expect(panel.locator('.compute-lease')).toContainText(job!.jobId);
    await expect(panel.locator('.compute-lease')).toContainText('预计释放');
    await expect(panel.locator('.compute-lease > span').filter({ hasText: '预计释放' })).not.toContainText('未知');
    const queue = page.getByRole('table', { name: '资源等待队列' });
    await expect(queue).toContainText(queued!.jobId); await expect(queue).not.toContainText(job!.jobId);
    const snapshot = await (await http.get(`${api}/api/v1/nodes`)).json();
    expect(snapshot.nodes[0].gpus[0].history.samples.some((sample: { utilization: number | null }) => sample.utilization === 0)).toBe(true);
    expect(snapshot.nodes[0].gpus[0].history.samples.some((sample: { utilization: number | null }) => sample.utilization === null)).toBe(true);
    await heartbeat(0, Date.now() / 1000); await page.getByRole('button', { name: '刷新资源' }).click();
    await expect(panel).toContainText('使用率 0%');
    await expect(panel.getByRole('meter', { name: 'Browser fixture GPU 显存使用' })).toHaveAttribute('value', '0');
    await page.screenshot({ path: info.outputPath('training-resources-light.png'), fullPage: true });
    await page.getByLabel('Color theme').selectOption('dark');
    await page.setViewportSize({ width: 900, height: 900 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath('training-resources-dark-900.png'), fullPage: true });
    await panel.getByRole('link', { name: job!.jobId, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/train/jobs/${job!.jobId}$`));
    await expect(page.getByRole('heading', { name: '训练作业详情', exact: true })).toBeVisible();
    await expect(page.getByTestId('job-detail')).toContainText(job!.jobId);
    expect((await (await http.get(`${api}/api/v1/jobs/${job!.jobId}`)).json()).status).toBe('running');
    expect(forbidden).toEqual([]); expect(errors).toEqual([]);
    await writeFile(info.outputPath('training-resources-receipt.json'), JSON.stringify({ planId: plan.id, task: task.id,
      job: job!.jobId, queued: queued!.jobId, nodes: snapshot.nodes, forbidden, errors, scope: 'GPU-free HTTP fake node, no trainer execution' }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script: owned, original,
      outputPath: name => info.outputPath(name), context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'fake-node:release-leases', run: async () => {
          if (queued) expect((await http.post(`${api}/api/v1/jobs/${queued.jobId}/cancel`)).ok()).toBe(true);
          if (job) {
            expect((await http.post(`${api}/api/v1/jobs/${job.jobId}/cancel`)).ok()).toBe(true);
            await heartbeat(0, Date.now() / 1000, [{ jobId: job.jobId, leaseId: job.leaseId, status: 'cancelled', elapsedSeconds: 13, iteration: 2 }]);
            expect((await (await http.get(`${api}/api/v1/nodes`)).json()).nodes[0].leases).toEqual({});
          }
        } }, { label: 'page:close', run: () => page.goto('about:blank') }, { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
