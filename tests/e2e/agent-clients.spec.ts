import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { writeFile } from 'node:fs/promises';
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

for (const scenario of ['complete', 'partial', 'exception'] as const) {
test(`approved Agent Plan exposes ${scenario} Run, scores and attributed cost`, async ({ page, playwright }, info) => {
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
  const errors: string[] = [], forbidden: string[] = [], requests: Record<string, unknown>[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (/\/api\/v1\/(sessions|tensorboard)(\/|$)|\/jobs\/[^/]+\/(view|logs)/.test(path)) forbidden.push(path);
    if (request.method() === 'POST' && /\/agents\/[^/]+\/runs$/.test(path)) requests.push(request.postDataJSON());
  });
  try {
    start('agent-client-api', python, ['-m', 'uvicorn', 'agent_clients_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('agent-client-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    let seeded = await http.post(`${api}/e2e/agent-plan?mode=${scenario === 'exception' ? 'expired' : scenario}`);
    expect(seeded.ok(), await seeded.text()).toBe(true);
    let plan = await seeded.json();
    if (scenario === 'exception') {
      expect((await http.post(`${api}/api/v1/approvals/${plan.approvalId}/decision`, { data: { decision: 'approve' } })).ok()).toBe(true);
      await expect.poll(async () => (await (await http.get(`${api}/api/v1/loops/${plan.goalId}`)).json()).budget.expired).toBe(true);
      seeded = await http.post(`${api}/e2e/agent-plan?mode=exception`);
      expect(seeded.ok(), await seeded.text()).toBe(true); plan = await seeded.json();
    }
    await page.goto(`${web}/approvals`);
    const approval = page.getByTestId(`approval-${plan.approvalId}`);
    await expect(approval).toContainText('Agent 运行计划');
    await expect(approval).toContainText('30 题次');
    await expect(approval).toContainText('llm/fixture');
    await expect(approval.getByText('Task 版本', { exact: true })).toHaveCount(0);
    await expect(approval.getByText('评估尝试', { exact: true })).toHaveCount(0);
    await approval.getByRole('button', { name: '批准', exact: true }).click();
    await expect(approval).toContainText('已批准');
    expect((await (await http.get(`${api}/api/v1/approvals/${plan.approvalId}`)).json()).status).toBe('approved');
    await page.goto(`${web}/plans/${plan.id}`);
    const launch = page.getByRole('button', { name: '启动 Agent 运行', exact: true });
    await expect(launch).toBeEnabled();
    let lostResponse = false;
    await page.route('**/api/v1/agents/*/runs', async route => {
      const response = await route.fetch();
      if (!lostResponse) { lostResponse = true; await route.abort('failed'); }
      else await route.fulfill({ response });
    });
    await launch.click();
    await expect(page.getByRole('alert')).toContainText('可在核对计划后重试');
    await launch.click();
    await expect(page).toHaveURL(/\/train\/jobs\/job-/);
    const detail = page.getByTestId('agent-run-detail');
    await expect(detail).toContainText(scenario === 'partial' ? 'provider_stream_incomplete' : '1,050');
    await expect(detail).toContainText(scenario === 'partial' ? '$0.00002' : '$0.00105');
    const cases = detail.getByRole('table', { name: 'Agent 题次结果' });
    await expect(cases.getByRole('row')).toHaveCount(scenario === 'partial' ? 2 : 31);
    if (scenario === 'partial') {
      await expect(cases).toContainText('未形成有效评分');
      await expect(cases.getByRole('cell', { name: '0', exact: true })).toHaveCount(0);
    }
    if (scenario === 'exception') {
      await expect(detail).toContainText('本计划获有限例外');
      await expect(detail).toContainText('目标期限已过');
      expect((await (await http.get(`${api}/api/v1/loops/${plan.goalId}`)).json()).budget.expired).toBe(true);
    }
    expect(requests).toHaveLength(2);
    expect(requests[0]).toEqual(requests[1]);
    expect(Object.keys(requests[0]).sort()).toEqual(['planId', 'planRevision', 'requestId', 'split', 'suite', 'version']);
    expect(requests[0]).toMatchObject({ planId: plan.id, planRevision: 1, suite: plan.suite, version: 1, split: 'train' });
    const jobs = (await (await http.get(`${api}/api/v1/jobs?kind=agent-run`)).json()).jobs;
    expect(jobs).toHaveLength(1); expect(jobs[0].usage.requests).toBe(scenario === 'partial' ? 1 : 30);
    const evidence = detail.getByRole('link', { name: /受信平台 Trace/ }).first();
    const url = await evidence.getAttribute('href');
    expect(url).toMatch(/^\/api\/v1\/agent-runs\/job-[^/]+\/artifacts\//);
    expect((await (await http.get(api + url)).json()).controlled).toBe(true);
    await page.getByLabel('Color theme').selectOption('dark');
    await page.screenshot({ path: info.outputPath('agent-run-dark.png'), fullPage: true });
    await detail.getByRole('link', { name: '查看本计划成本', exact: true }).click();
    await expect(page.getByLabel('成本计划', { exact: true })).toHaveValue(`${plan.id}@1`);
    await expect(page.getByTestId('cost-selected-tokens')).toContainText(scenario === 'partial' ? '未知' : '1,050');
    await expect(page.getByTestId('cost-selected-llm')).toContainText(scenario === 'partial' ? '未知' : '$0.00105');
    const tokens = page.getByTestId('token-cost-chart');
    await tokens.locator('summary').click();
    await expect(tokens.getByRole('columnheader', { name: '缓存写入', exact: true })).toBeVisible();
    if (scenario === 'partial') {
      await expect(page.getByLabel('当前范围成本摘要')).toContainText('已知部分 $0.00002');
      await expect(page.getByLabel('当前范围成本摘要')).toContainText('未确认预留 $0.0001');
      await expect(tokens.getByRole('table', { name: 'Token 数据表' })).toContainText('未知');
      expect((await tokens.locator('rect[data-coverage="partial"]').count())).toBeGreaterThan(0);
    } else {
      for (const amount of ['600', '300', '60', '90']) await expect(tokens.getByRole('cell', { name: amount, exact: true })).toBeVisible();
    }
    await page.setViewportSize({ width: 900, height: 1000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath('agent-cost-dark-900.png'), fullPage: true });
    await page.goto(`${web}/eval/${plan.suite.replace(/^suite\//, '')}`);
    if (scenario === 'partial') {
      await expect(page.getByRole('table', { name: 'Agent 逐题得分' })).toContainText('未形成有效评分');
      await expect(page.getByRole('table', { name: 'Agent 评估计分板' })).toContainText('尚无 Agent train 运行记录');
    } else {
      await expect(page.getByRole('table', { name: 'Agent 评估计分板' })).toContainText('78.67 / 100');
      await expect(page.getByRole('table', { name: 'Agent 评估计分板' })).toContainText('reported');
      await expect(page.getByRole('table', { name: 'Agent 逐题得分' }).getByRole('row')).toHaveCount(31);
    }
    await expect(page.getByTestId('held-out-test-row')).toContainText('锁定');
    await page.screenshot({ path: info.outputPath('agent-eval-dark-900.png'), fullPage: true });
    await page.goto(`${web}/train?layer=agent`);
    await expect(page.getByRole('table', { name: 'Agent 运行作业表' })).toContainText(jobs[0].jobId);
    await expect(page.getByTestId('goal-notebook-panel')).toHaveCount(0);
    expect((await (await http.get(`${api}/api/v1/health`)).json()).sessionCount).toBe(0);
    expect(errors).toEqual([]); expect(forbidden).toEqual([]);
    await writeFile(info.outputPath('client-receipt.json'), JSON.stringify({ planId: plan.id, requests,
      jobs, forbidden, errors, scope: 'Controlled scores/final states; actual Plan/approval/admission/stores/model gate/HTTP/UI, no Source/SDK/external provider' }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script, original, outputPath: name => info.outputPath(name),
      context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'page:close', run: () => page.goto('about:blank') }, { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
}
