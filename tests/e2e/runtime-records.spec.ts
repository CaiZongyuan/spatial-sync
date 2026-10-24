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

test('existing native runtime file imports, grows and retries with messages and metering intact', async ({ page, playwright }, info) => {
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
  const http = await playwright.request.newContext(), errors: string[] = [], forbidden: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('websocket', socket => forbidden.push(socket.url()));
  page.on('request', request => {
    if (/\/api\/v1\/(sessions|evaluations|jobs|agents\/[^/]+\/runs)(\/|$)/.test(new URL(request.url()).pathname)) forbidden.push(request.url());
  });
  try {
    start('runtime-api', python, ['-m', 'uvicorn', 'runtime_records_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('runtime-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    const capture = await readFile(resolve('../../tests/fixtures/runtime/claude-code-stream.jsonl'), 'utf8');
    const lines = capture.trim().split('\n'), upload = async (content: string) => {
      await page.getByLabel('原生运行记录文件').setInputFiles({ name: 'native-capture.jsonl', mimeType: 'application/x-ndjson', buffer: Buffer.from(content) });
      const response = page.waitForResponse(response => response.url().endsWith('/api/v1/runtime-sessions/import'));
      await page.getByRole('button', { name: '导入 / 刷新文件', exact: true }).click();
      const received = await response; expect(received.status(), await received.text()).toBe(202);
      return received.json();
    };
    await page.goto(`${web}/runtime-sessions`);
    const partial = await upload(lines.slice(0, 3).join('\n'));
    await expect(page).toHaveURL(new RegExp(`/runtime-sessions/${partial.session.id}$`));
    const detail = page.getByTestId('runtime-detail');
    await expect(detail.getByRole('table', { name: 'Runtime Token 明细' })).toContainText('0');
    expect(partial.session.usage.cacheReadTokens).toBe(0); expect(partial.session.usage.llmUsd).toBeNull();
    const full = await upload(capture);
    expect(full.session.id).toBe(partial.session.id); expect(full.addedEvents).toBe(1);
    await expect(detail).toContainText('$0.3244434');
    await expect(detail).toContainText('运行时轮次 20');
    await expect(detail).toContainText('mcp__lab__library_search');
    await expect(detail).toContainText('Minimized result from the captured laboratory tool call.');
    await expect(detail).toContainText('运行时估算（原样）');
    await expect(detail.getByRole('table', { name: 'Runtime Token 明细' }).getByRole('cell')).toHaveText(['32', '2,680', '278,457', '26,878', '未知', '20']);
    expect(full.session.usage.tokens).toBeNull(); expect(full.session.usage.requests).toBeNull();
    expect(full.session.associations).toEqual({}); expect(full.session.thinking).toBeNull();
    await expect(detail).toContainText('未关联');
    await expect(page.getByRole('table', { name: 'Runtime 会话列表' }).getByRole('row')).toHaveCount(2);
    const repeated = await upload(capture);
    expect(repeated.idempotent).toBe(true); expect(repeated.addedEvents).toBe(0);
    for (const artifact of full.session.artifacts) {
      await expect(detail.locator(`a[href="${artifact.url}"]`)).toHaveCount(1);
      const evidence = await http.get(`${api}${artifact.url}`); expect(evidence.ok()).toBe(true);
      expect(evidence.headers()['content-type']).toMatch(/json|ndjson/);
    }
    const costs = await (await http.get(`${api}/api/v1/cost/summary?range=7d`)).json();
    expect(costs.totals.llmUsd).toBe(.3244434); expect(costs.totals.runtimeSessionCount).toBe(1);
    expect(costs.totals.requests).toBeNull(); expect(costs.totals.tokens).toBeNull();
    expect(costs.totals.timeBasis).toBe('importedAt'); expect(costs.choices.goal).toEqual([]);
    await detail.getByRole('link', { name: '打开成本总览' }).click();
    await expect(page.getByTestId('cost-selected-llm')).toContainText('$0.3244434');
    await expect(page.getByTestId('cost-selected-llm')).toContainText('运行时估算（原样）');
    await expect(page.getByTestId('token-cost-chart').getByRole('img', { name: /四类 Token 堆叠柱/ })).toBeVisible();
    await expect(page.getByRole('table', { name: '按 RSI 目标分摊' })).toContainText('未关联');
    await expect(page.getByTestId('gpu-cost-chart').locator('.cost-gpu-group')).toHaveCount(0);
    await expect(page.getByTestId('gpu-cost-chart')).toContainText('未接入计算节点');
    await page.screenshot({ path: info.outputPath('runtime-cost.png'), fullPage: true });
    await page.getByRole('link', { name: '查看运行记录与来源', exact: true }).click();
    await page.getByRole('table', { name: 'Runtime 会话列表' }).getByRole('link', { name: 'native-capture.jsonl', exact: true }).click();
    await page.reload(); await expect(detail).toContainText('$0.3244434');
    await page.getByLabel('Color theme').selectOption('dark');
    await page.screenshot({ path: info.outputPath('runtime-records-dark.png'), fullPage: true });
    await page.setViewportSize({ width: 900, height: 1000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(page.getByRole('button', { name: /^(Start|Cancel|Cleanup)$/i })).toHaveCount(0);
    expect((await (await http.get(`${api}/api/v1/health`)).json()).sessionCount).toBe(0);
    expect(forbidden).toEqual([]); expect(errors).toEqual([]);
    await writeFile(info.outputPath('runtime-records-receipt.json'), JSON.stringify({ partial, full, repeated, costs, errors, forbidden,
      scope: 'Existing actual-format Claude Code output; real HTTP/store/UI; no model, SDK or Source execution' }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script, original, outputPath: name => info.outputPath(name),
      context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'page:close', run: () => page.goto('about:blank') }, { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
