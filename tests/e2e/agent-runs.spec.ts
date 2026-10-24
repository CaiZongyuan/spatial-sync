import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
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

test('external evaluation freezes inputs, exposes real Synthetic evidence and keeps reported scores separate', async ({ page, playwright }, info) => {
  test.setTimeout(60_000);
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
  const http = await playwright.request.newContext(), errors: string[] = [];
  let runId: string | undefined;
  page.on('pageerror', error => errors.push(error.message));
  try {
    start('agent-api', python, ['-m', 'uvicorn', 'apps.api.app.main:app', '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('agent-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    await page.goto(`${web}/eval/lab-ops-basic@1`);
    await expect(page.getByLabel('评估 Harness 版本')).toHaveValue('1');
    await page.getByLabel('Runtime 名称', { exact: true }).fill('claude-code');
    await page.getByLabel('Runtime 版本', { exact: true }).fill('2.1.306');
    await page.getByLabel('模型声明', { exact: true }).fill('claude-opus-5-5');
    await page.getByLabel('思考等级声明', { exact: true }).fill('high');
    await page.getByLabel('平台期限（秒）').fill('120');
    const response = page.waitForResponse(response => response.url().endsWith('/agents/lab-operator/runs') && response.request().method() === 'POST');
    await page.getByRole('button', { name: '创建完整 train 评估', exact: true }).click();
    const createdResponse = await response; expect(createdResponse.ok(), await createdResponse.text()).toBe(true);
    const created = await createdResponse.json(); runId = created.id;
    expect(created.runtime).toEqual({ name: 'claude-code', version: '2.1.306', model: 'claude-opus-5-5', thinking: 'high' });
    expect(created.purpose).toBe('train'); expect(created.harnessVersion).toBe(1);
    const detail = page.getByTestId('agent-run-detail');
    await expect(detail).toContainText(runId!);
    await expect(detail).toContainText(`lw agent-run next ${runId} --json`);
    let handoff: any;
    await expect.poll(async () => {
      const result = await http.post(`${api}/api/v1/agent-runs/${runId}/next`);
      expect(result.ok(), await result.text()).toBe(true); handoff = await result.json();
      return handoff.status;
    }).toBe('work');
    const work = handoff.work;
    const capture = await readFile(resolve('../../tests/fixtures/runtime/claude-code-stream.jsonl'), 'utf8');
    const imported = await http.post(`${api}/api/v1/runtime-sessions/import`, { data: {
      format: 'claude-code-stream-json', content: capture, sourceLabel: 'Actual-format fixture replay; scripted Synthetic case, no model execution',
    } });
    expect(imported.ok(), await imported.text()).toBe(true);
    const native = (await imported.json()).session;
    // A scripted controller signals completion without claiming the task succeeded.
    const completed = await http.post(`${api}/api/v1/agent-runs/${runId}/complete`, { data: {
      caseRunId: work.caseRunId, nativeSessionId: native.nativeSessionId, runtimeRecordId: native.id,
    } });
    expect(completed.ok(), await completed.text()).toBe(true);
    const outcome = await completed.json();
    expect(outcome.case.status).toBe('completed'); expect(outcome.case.score).toBeGreaterThanOrEqual(0); expect(outcome.case.score).toBeLessThanOrEqual(100);
    await page.getByRole('button', { name: '刷新运行', exact: true }).click();
    const cases = page.getByRole('table', { name: 'Agent 逐题结果', exact: true });
    await expect(cases).toContainText(`${outcome.case.score.toFixed(1)} / 100`);
    await expect(cases).toContainText('$0.3244434'); await expect(cases).toContainText('完成时快照');
    await expect(cases.getByRole('link', { name: '查看原生运行记录 ↗' })).toHaveAttribute('href', `/runtime-sessions/${native.id}`);
    await expect(cases.getByRole('link', { name: '查看回放 ↗' })).toBeVisible();
    for (const artifact of outcome.case.artifacts) {
      const evidence = await http.get(`${api}${artifact.url}`); expect(evidence.ok()).toBe(true);
      expect(await evidence.text()).not.toContain(work.connection.token);
      await expect(cases.locator(`a[href="${artifact.url}"]`)).toBeVisible();
    }
    await expect(detail).not.toContainText(work.connection.token);
    await page.getByRole('button', { name: '取消评估', exact: true }).click();
    await expect(detail).toContainText('已取消');
    await expect(detail).toContainText('已确认完成');
    await expect(page.getByRole('table', { name: 'Agent 评估计分板', exact: true })).toContainText('尚无完整有效');
    expect((await (await http.get(`${api}/api/v1/health`)).json()).sessionCount).toBe(0);
    await page.screenshot({ path: info.outputPath('actual-synthetic-partial-cancel.png'), fullPage: true });

    // These final records are explicitly controlled UI contracts, not a completed runtime evaluation.
    const row = { evaluationId: 'controlled-score-fixture', suite: created.suite, subject: created.agentId, version: 1,
      key: { model: 'claude-opus-5-5', thinking: 'high' }, conditionsGroup: 'controlled-ui-only', split: 'train',
      score: 82.5, ci95: null, episodes: 30, cases: 10, runs: 3, decision: 'reported', recordings: [], traces: [],
      cost: { source: 'runtime-import', observationBasis: 'completion-snapshot', llmUsd: .3244434, tokens: null,
        requests: null, costSource: 'runtime-estimate', runtimeRecordIds: [native.id] },
      durationSeconds: 60, at: created.at };
    const fixture = { ...outcome.run, id: row.evaluationId, evaluationId: row.evaluationId, status: 'completed', result: row,
      cleanup: { complete: true, blocked: false }, cases: [outcome.case], error: null };
    await page.route('**/api/v1/suites/lab-ops-basic/1/scoreboard', route => route.fulfill({ json: { rows: [row] } }));
    let showInvalid = false;
    const invalid = { ...fixture, id: 'controlled-invalid-fixture', evaluationId: 'controlled-invalid-fixture',
      status: 'failed', result: null, cleanup: { complete: false, blocked: true }, error: { code: 'source_cleanup_unconfirmed' },
      cases: [{ ...outcome.case, score: null, status: 'invalid', usage: null, runtimeRecordId: null, runtimeRecordHash: null,
        nativeSessionId: null, error: { code: 'evidence_unavailable' } }] };
    await page.route('**/api/v1/agent-runs', route => route.fulfill({ json: { runs: [showInvalid ? invalid : fixture] } }));
    await page.reload();
    const scoreboard = page.getByRole('table', { name: 'Agent 评估计分板', exact: true });
    await expect(scoreboard).toContainText('82.5 / 100'); await expect(scoreboard).not.toContainText('8250');
    await expect(scoreboard).toContainText('30 次实验'); await expect(scoreboard).toContainText('已报告');
    await expect(scoreboard).toContainText('运行时估算（原样）'); await expect(scoreboard).toContainText('Token 未知');
    await expect(page.getByTestId('held-out-test-row')).toContainText('锁定');
    await expect(page.getByRole('img', { name: '条件 1 的评分曲线，1 次评估' })).toBeVisible();
    await expect(page.locator('.eval-reference-label')).toHaveCount(0);
    await page.getByLabel('Color theme').selectOption('dark');
    await page.screenshot({ path: info.outputPath('controlled-reported-score-dark.png'), fullPage: true });
    await page.setViewportSize({ width: 900, height: 1000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    showInvalid = true;
    await page.getByRole('button', { name: '刷新运行', exact: true }).click();
    await expect(detail).toContainText('未确认，单活跃 Session 槽仍被占用');
    await expect(detail).toContainText('基础设施无效'); await expect(detail).toContainText('evidence_unavailable');
    await expect(detail).toContainText('此运行没有完整有效的 train 评分');
    await expect(detail).toContainText('未关联运行时记录，用量未知');
    await expect(detail.getByRole('button', { name: '取消评估', exact: true })).toHaveCount(0);
    await page.screenshot({ path: info.outputPath('controlled-invalid-cleanup.png'), fullPage: true });
    expect(errors).toEqual([]);
    await writeFile(info.outputPath('agent-runs-receipt.json'), JSON.stringify({ created, outcome, errors,
      actualScope: 'Real HTTP freeze/handoff, one actual Synthetic graded case, explicit native-format fixture metering, cancel and cleanup; no external model execution',
      controlledScope: '82.5/100 completed scoreboard/chart and invalid/blocked-cleanup states are labelled UI contract fixtures, not 10×3 execution evidence' }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script, original, outputPath: name => info.outputPath(name),
      context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'run:cancel', run: async () => { if (runId) await http.post(`${api}/api/v1/agent-runs/${runId}/cancel`); } },
        { label: 'page:close', run: () => page.goto('about:blank') }, { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
