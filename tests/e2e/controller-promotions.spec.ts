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

test('human Controller review warns, resumes approved publication and reads exact resource history', async ({ page, playwright }, info) => {
  test.setTimeout(120_000);
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
  page.on('pageerror', error => errors.push(error.message));
  try {
    start('promotion-api', python, ['-m', 'uvicorn', 'controller_promotion_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('promotion-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    const bootstrap = String.raw`
import json, sys
from pathlib import Path
import httpx
from tests.fixtures.policy import observation_spec
from tests.gateway.test_policies import uploaded_job
spec = observation_spec()
spec['observations'][0]['keys'] = spec['action']['jointKeys'] = ['/World/Lab/RobotA/fr3/fr3_joint1']
with httpx.Client(base_url=sys.argv[1], trust_env=False, timeout=30) as client:
    job = uploaded_job(client, Path(sys.argv[2]), spec=spec)
    policy = client.post('/api/v1/policies', json={'checkpoint': job['jobId'] + '/checkpoints/model_0.pt'})
    policy.raise_for_status()
    policy = policy.json()
    seeded = client.post('/fixture/controller-promotions/train', json={'policy': policy['id']})
    seeded.raise_for_status()
    requested = client.post('/api/v1/controller-promotions', json={'subject': policy['id'], 'scene': policy['scene'], 'requestId': 'browser-controlled-promotion'})
    requested.raise_for_status()
    promotion = requested.json()
    completed = client.post('/fixture/controller-promotions/' + promotion['id'] + '/complete-tests')
    completed.raise_for_status()
    print(json.dumps(completed.json()))
`;
    const seeded = JSON.parse((await execute(python, ['-c', bootstrap, api, info.outputPath('bootstrap')],
      { cwd: resolve('../..'), env, timeout: 30_000 })).stdout);
    expect(seeded.evidenceMode).toBe('controlled'); expect(seeded.sourceStarts).toBe(0);
    await http.post(`${api}/fixture/controller-promotions/publication-fault`, { data: { active: true } });
    await page.goto(`${web}/approvals`);
    const card = page.getByTestId(`approval-${seeded.approvalId}`);
    await expect(card.getByRole('heading', { name: '人类部署评估' })).toBeVisible();
    await expect(card).toContainText('受控工程证据');
    await expect(card.getByRole('alert').filter({ hasText: 'held-out 成功率低于 Reference' })).toBeVisible();
    await expect(card).toContainText('100.0%'); await expect(card).toContainText('0.0%');
    const response = page.waitForResponse(value => value.url().endsWith(`/approvals/${seeded.approvalId}/decision`));
    await card.getByRole('button', { name: '批准', exact: true }).click();
    expect((await response).status()).toBe(500);
    await expect(card).toContainText('已批准，发布尚未完成');
    const resume = card.getByRole('button', { name: '重试 Controller 发布', exact: true });
    await expect(resume).toBeEnabled();
    const before = await (await http.get(`${api}/api/v1/controllers/fr3-arm`)).json();
    expect(before.currentVersion).toBeNull();
    await page.screenshot({ path: info.outputPath('controller-promotion-awaiting-recovery.png'), fullPage: true });
    await http.post(`${api}/fixture/controller-promotions/publication-fault`, { data: { active: false } });
    await resume.click();
    await expect.poll(async () => (await (await http.get(`${api}/api/v1/controllers/fr3-arm`)).json()).currentVersion).toBe(1);
    const approval = await (await http.get(`${api}/api/v1/approvals/${seeded.approvalId}`)).json();
    expect(approval.decisions).toHaveLength(1); expect(approval.canRevoke).toBe(false);
    await page.goto(`${web}/resources/robots`);
    const robot = page.locator('[data-embodiment="fr3-sim"]');
    await expect(robot).toContainText('Controller fr3-arm · v1');
    await expect(robot).toContainText('policy/training-probe@1'); await expect(robot).toContainText('30 Hz');
    await expect(robot).toContainText('默认部署场景'); await expect(robot).toContainText('robot-and-tubes@3');
    await expect(robot.getByRole('table')).toContainText('eval-controlled-candidate');
    await page.setViewportSize({ width: 900, height: 1000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath('controller-published-resources.png'), fullPage: true });
    expect((await (await http.get(`${api}/api/v1/health`)).json()).sessionCount).toBe(0); expect(errors).toEqual([]);
    await writeFile(info.outputPath('controller-promotion-receipt.json'), JSON.stringify({
      scope: 'Controlled scores, qualification and physical fixture; actual HTTP approval/publication/UI; zero Source or SDK execution', seeded, approval, errors,
    }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script, original, outputPath: name => info.outputPath(name),
      context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'page:close', run: () => page.goto('about:blank') }, { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
