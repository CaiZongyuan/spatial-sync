import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { cleanupOwnedProcesses, finishCleanup, type OwnedProcess } from './owned-cleanup';

declare global {
  interface Window {
    __trainingSocketsFixture: { count(): number; close(): void };
  }
}

async function freePort() {
  return new Promise<number>((resolve, reject) => {
    const server = createServer(); server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as { port: number }).port;
      server.close(error => error ? reject(error) : resolve(port));
    });
  });
}

test('public fake node renders four isolated Job views, reconnects, resets and releases terminal geometry', async ({ page, playwright }, info) => {
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
    return record;
  };
  const env = { ...process.env, SPATIAL_REMOTE: '0', SPATIAL_ALLOWED_ORIGINS: web, SPATIAL_API_URL: api,
    SPATIAL_NODE_TRAINER: '[]', SPATIAL_LIVE_BINDINGS: '', LW_LIBRARY: info.outputPath('library'),
    SPATIAL_STATE_ROOT: info.outputPath('state'), SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings') };
  const http = await playwright.request.newContext();
  const token = 'training-resources-browser-fixture-token', instance = 'training-resources-browser-node';
  const headers = { 'X-Node-Token': token, 'X-Node-Instance': instance };
  let node: object, job: { jobId: string; leaseId: string } | undefined;
  const forbidden: string[] = [], errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/\/api\/v1\/(sessions|recordings)(\/|$)/.test(new URL(request.url()).pathname)) forbidden.push(request.url()); });
  page.on('websocket', socket => { if (socket.url().includes('/sessions/')) forbidden.push(socket.url()); });
  // Keep manifest/binary traffic on the actual browser wire. Routing every frame
  // through the test driver adds a second transport for all four environments.
  await page.addInitScript(() => {
    const NativeWebSocket = window.WebSocket;
    const sockets: WebSocket[] = [];
    Object.defineProperty(window, '__trainingSocketsFixture', { value: {
      count: () => sockets.length,
      close: () => sockets.at(-1)!.close(4001, 'Controlled reader reconnect'),
    } });
    window.WebSocket = class extends NativeWebSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);
        if (/\/api\/v1\/jobs\/[^/]+\/view$/.test(new URL(String(url), location.href).pathname)) sockets.push(this);
      }
    };
  });
  const debug = () => page.evaluate(() => window.__trainingViewDebug?.read());
  try {
    start('view-api', python, ['-m', 'uvicorn', 'training_resources_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], resolve('../..'), env);
    start('view-web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd(), env);
    await expect.poll(async () => {
      for (const process of processes) if (process.error || process.child.exitCode !== null) throw process.error ?? new Error(process.output);
      try { return (await http.get(`${api}/api/v1/health`, { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 20_000 }).toBe(true);
    const { stdout } = await execute(python, [resolve('../../tests/fixtures/training_task.py'), `view-${randomUUID()}`], { encoding: 'buffer' });
    const uploaded = await http.post(`${api}/api/v1/tasks`, { headers: { 'Content-Type': 'application/x-tar' }, data: stdout });
    expect(uploaded.ok(), await uploaded.text()).toBe(true);
    const task = await uploaded.json();
    const submitted = await http.post(`${api}/api/v1/plans`, { data: {
      kind: 'training', title: 'GPU-free Job view transport test', goal: 'Exercise four real view regions with controlled frames', task: task.id,
      algorithm: { library: 'rsl_rl', name: 'PPO', version: '5.4.1' }, model: { actor: [32], critic: [32] },
      hyperparameters: { num_envs: 4, max_iterations: 10 }, jobs: { seeds: [42] }, budget: { gpuHours: 1 },
      evaluation: { scene: 'robot-and-tubes@2', episodes: 1, threshold: 1 }, rationale: 'Transport fixture only; no physics or training.',
      hypotheses: [{ id: 'H32', cause: 'Need independent view state', change: 'Supply controlled complete frames', predict: { metric: 'visible.envs', to: '=4' } }],
      notebookRead: [], interview: 'Owned test approval client; no business-plan approval is claimed.',
    } });
    expect(submitted.ok(), await submitted.text()).toBe(true);
    const plan = await submitted.json();
    expect((await http.post(`${api}/api/v1/approvals/${plan.approvalId}/decision`, { data: { decision: 'approve' } })).ok()).toBe(true);
    node = await (await http.get(`${api}/e2e/training-profile/${plan.id}`)).json();
    expect((await http.post(`${api}/api/v1/nodes/local/heartbeat`, { headers, data: node })).ok()).toBe(true);
    const started = await http.post(`${api}/api/v1/jobs`, { data: { planId: plan.id, seed: 42 } });
    expect(started.status(), await started.text()).toBe(202); job = await started.json();
    expect((await http.post(`${api}/api/v1/nodes/local/heartbeat`, { headers, data: { ...node, reports: [
      { jobId: job!.jobId, leaseId: job!.leaseId, status: 'running', iteration: 1, elapsedSeconds: 1, meanReward: 0 },
    ] } })).ok()).toBe(true);
    const producer = start('public-controlled-producer', python, [resolve('../../tests/fixtures/training_view_node.py'), api, job!.jobId, job!.leaseId, token, instance], resolve('../..'), env);
    await expect.poll(() => producer.output).toContain('"ready": true');
    await page.setViewportSize({ width: 1600, height: 1700 });
    await page.goto(`${web}/train/jobs/${job!.jobId}`);
    await page.getByLabel('Color theme').selectOption('dark');
    const send = (command: object) => producer.child.stdin!.write(JSON.stringify(command) + '\n');
    send({ type: 'sample', seq: 0 });
    await expect(page.locator('canvas')).toHaveCount(1);
    await expect.poll(async () => Object.keys((await debug())?.environments ?? {})).toEqual(['0', '1', '2', '3']);
    await expect.poll(async () => Object.values((await debug())!.environments).map(row => row.rendered['/World/Lab/RobotA/fr3/base']?.p[0])).toEqual([0, 0.1, 0.2, 0.3]);
    // Observe actual pixels: SceneContents useFrame can run for an erroneously culled View.
    const boxes = await page.locator('.training-environment-view').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()));
    const screenshot = await page.screenshot({ path: info.outputPath('four-view-pixels.png'), scale: 'css' });
    const visiblePixels = await page.evaluate(async ({ encoded, boxes }) => {
      const image = new Image(); image.src = `data:image/png;base64,${encoded}`; await image.decode();
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const context = canvas.getContext('2d')!; context.drawImage(image, 0, 0);
      return boxes.map(box => {
        const pixels = context.getImageData(Math.ceil(box.x), Math.ceil(box.y), Math.floor(box.width), Math.floor(box.height)).data;
        let bright = 0; for (let i = 0; i < pixels.length; i += 4) if (pixels[i] > 140 && pixels[i + 1] > 140 && pixels[i + 2] > 140) bright++;
        return bright;
      });
    }, { encoded: screenshot.toString('base64'), boxes });
    expect(visiblePixels.every(count => count > 100), JSON.stringify(visiblePixels)).toBe(true);
    const key = '/World/Lab/Tubes/Tube01';
    await page.getByLabel('环境 0 实体').selectOption(key);
    send({ type: 'sample', seq: 1, reset: 1 });
    await expect.poll(async () => Object.values((await debug())!.environments).map(row => [row.seq, row.epoch])).toEqual([[1, 0], [1, 1], [1, 0], [1, 0]]);
    const before = (await debug())!;
    await page.evaluate(() => window.__trainingSocketsFixture.close());
    await expect.poll(() => page.evaluate(() => window.__trainingSocketsFixture.count())).toBe(2);
    await expect.poll(async () => (await debug())?.state).toBe('live');
    expect((await debug())!.viewId).toBe(before.viewId);
    expect((await debug())!.environments[0].selected).toBe(key);
    expect((await debug())!.environments[1]).toMatchObject({ seq: 1, epoch: 1 });
    send({ type: 'restart', incarnation: 2 }); send({ type: 'sample', seq: 0 });
    await expect.poll(async () => (await debug())?.viewId).toBe('fake-view-2');
    await expect.poll(async () => Object.values((await debug())!.environments).map(row => [row.seq, row.epoch, row.selected])).toEqual([[0, 0, ''], [0, 0, ''], [0, 0, ''], [0, 0, '']]);
    expect((await http.get(`${api}/api/v1/health`)).ok()).toBe(true);
    expect((await (await http.get(`${api}/api/v1/health`)).json()).sessionCount).toBe(0);
    expect(forbidden).toEqual([]); expect(errors).toEqual([]);
    await writeFile(info.outputPath('view-receipt.json'), JSON.stringify({ visiblePixels, before, after: await debug(), forbidden, errors, scope: 'Public GPU-free fake producer; no training or physics claim' }));
  } catch (error) { original.push(error); }
  finally {
    const cleanup = await cleanupOwnedProcesses(processes, { python, script: owned, original, outputPath: name => info.outputPath(name),
      context: { source: resolve('../..'), api, web }, ports: [apiPort, webPort], prepare: [
        { label: 'fake-node:terminal', run: async () => {
          if (job) {
            expect((await http.post(`${api}/api/v1/jobs/${job.jobId}/cancel`)).ok()).toBe(true);
            expect((await http.post(`${api}/api/v1/nodes/local/heartbeat`, { headers, data: { ...node, reports: [
              { jobId: job.jobId, leaseId: job.leaseId, status: 'cancelled', elapsedSeconds: 2, iteration: 1 },
            ] } })).ok()).toBe(true);
            if (!original.length) await expect(page.locator('canvas')).toHaveCount(0);
          }
        } }, { label: 'page:close', run: () => page.goto('about:blank') }, { label: 'http:dispose', run: () => http.dispose() },
      ] });
    finishCleanup(cleanup, original);
  }
});
