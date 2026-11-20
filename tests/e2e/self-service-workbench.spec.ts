import { expect, test } from '@playwright/test';
import { execFile, spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { resolve } from 'node:path';
import { promisify } from 'node:util';
import { cleanupOwnedProcesses, finishCleanup, type OwnedProcess } from './owned-cleanup';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('scene draft saves a pinned asset pose and the moved ground Waypoint', async ({ page, request }) => {
  const assetResponse = await request.get(`${api}/api/v1/library/assets/robot/microduck/1`);
  expect(assetResponse.ok(), await assetResponse.text()).toBe(true);
  const asset = await assetResponse.json();
  await page.goto('/scenes');
  await page.getByRole('link', { name: '新建场景草稿' }).click();
  await page.getByLabel('场景名称').fill('browser-layout');
  await page.getByLabel('标题', { exact: true }).fill('Browser layout');
  await page.getByLabel('素材版本').selectOption(asset.id);
  await page.getByRole('button', { name: '添加素材' }).click();
  await page.getByLabel('X（m）').fill('1.25');
  await page.getByLabel('朝向（°）').fill('90');

  const ground = page.getByRole('group', { name: '场景地面平面' });
  await page.getByRole('button', { name: '点选地面添加 Waypoint' }).click();
  const bounds = (await ground.boundingBox())!;
  await page.mouse.click(bounds.x + bounds.width * .3, bounds.y + bounds.height * .7);
  const marker = (await ground.getByRole('button', { name: 'Waypoint Waypoint_1' }).boundingBox())!;
  const x = marker.x + marker.width / 2, y = marker.y + marker.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + bounds.width * .1, y - bounds.height * .1, { steps: 5 });
  await page.mouse.up();

  await page.getByRole('button', { name: '保存场景草稿' }).click();
  await expect(page).toHaveURL(/draft=draft-[a-f0-9]{64}/);
  const id = new URL(page.url()).searchParams.get('draft');
  const saved = (await (await request.get(`${api}/api/v1/library/scene-drafts/${id}`)).json()).draft;
  expect(saved.assets).toEqual([expect.objectContaining({
    asset: asset.assetVersion, contentHash: asset.contentHash, position: [1.25, 0, 0],
  })]);
  expect(saved.assets[0].orientation[2]).toBeCloseTo(Math.SQRT1_2);
  expect(saved.assets[0].orientation[3]).toBeCloseTo(Math.SQRT1_2);
  expect(saved.waypoints.Waypoint_1.p[0]).toBeCloseTo(-1, 1);
  expect(saved.waypoints.Waypoint_1.p[1]).toBeCloseTo(-1, 1);
  expect(saved.waypoints.Waypoint_1.p[2]).toBe(0);
  await page.reload();
  await expect(page.getByRole('group', { name: '场景地面平面' }).getByRole('button', { name: 'Waypoint Waypoint_1' }))
    .toBeVisible();
});

async function freePort() {
  return new Promise<number>((resolve, reject) => {
    const socket = createServer();
    socket.once('error', reject);
    socket.listen(0, '127.0.0.1', () => {
      const port = (socket.address() as { port: number }).port;
      socket.close(error => error ? reject(error) : resolve(port));
    });
  });
}

test('registered node drives the pending approval estimate and the exact Policy Export', async ({ browser, playwright }, info) => {
  const [apiPort, webPort] = await Promise.all([freePort(), freePort()]);
  const gateway = `http://127.0.0.1:${apiPort}`, web = `http://127.0.0.1:${webPort}`;
  const root = resolve('../..'), python = process.env.SPATIAL_PYTHON ?? resolve(root, '.venv/bin/python');
  const script = resolve(root, 'tests/fixtures/owned_processes.py'), execute = promisify(execFile);
  const env = { ...process.env, SPATIAL_REMOTE: '1', SPATIAL_OPERATOR_TOKEN: 'workbench-operator-fixture',
    SPATIAL_VIEWER_TOKEN: 'workbench-viewer-fixture', SPATIAL_ALLOWED_ORIGINS: web, SPATIAL_API_URL: gateway,
    SPATIAL_NODE_TRAINER: '[]', SPATIAL_NODE_MJLAB_TRAINER: '[]', SPATIAL_LIVE_BINDINGS: '',
    LW_LIBRARY: info.outputPath('library'), SPATIAL_STATE_ROOT: info.outputPath('state'),
    SPATIAL_RECORDINGS_ROOT: info.outputPath('recordings'), LW_TASK_AUDIT_FILE: info.outputPath('audits.json') };
  const children: OwnedProcess[] = [], original: unknown[] = [];
  function start(label: string, executable: string, args: string[], cwd: string) {
    const child = spawn(executable, args, { cwd, env, windowsHide: true });
    const owner: OwnedProcess = { child, label, output: '', exited: new Promise(resolve => child.once('exit', () => resolve())),
      identity: execute(python, [script, 'snapshot', String(child.pid)]).then(value => ({ identity: value.stdout }), error => ({ error })) };
    child.stdout?.on('data', chunk => { owner.output += chunk; });
    child.stderr?.on('data', chunk => { owner.output += chunk; });
    child.once('error', error => { owner.error = error; });
    children.push(owner);
  }
  const operator = { Authorization: `Bearer ${env.SPATIAL_OPERATOR_TOKEN}` };
  const viewer = { Authorization: `Bearer ${env.SPATIAL_VIEWER_TOKEN}` };
  const browserOperator = await browser.newContext({ extraHTTPHeaders: operator });
  const browserViewer = await browser.newContext({ extraHTTPHeaders: viewer });
  const http = await playwright.request.newContext({ baseURL: gateway });
  try {
    start('api', python, ['-m', 'uvicorn', 'approval_gateway:app', '--app-dir', 'tests/fixtures',
      '--host', '127.0.0.1', '--port', String(apiPort)], root);
    start('web', process.execPath, [resolve('node_modules/vite/bin/vite.js'),
      ...(process.env.SPATIAL_E2E_WEB_MODE === 'preview' ? ['preview'] : []),
      '--host', '127.0.0.1', '--port', String(webPort), '--strictPort'], process.cwd());
    await expect.poll(async () => {
      for (const owner of children) if (owner.error || owner.child.exitCode !== null) throw owner.error ?? Error(owner.output);
      try { return (await http.get('/api/v1/health', { timeout: 500 })).ok() && (await http.get(web, { timeout: 500 })).ok(); }
      catch { return false; }
    }, { timeout: 15_000 }).toBe(true);
    const roots = await (await http.get('/e2e/storage-roots')).json();
    for (const path of Object.values(roots) as string[]) expect(resolve(path).startsWith(resolve(info.outputDir))).toBe(true);

    const page = await browserOperator.newPage(), observer = await browserViewer.newPage();
    await page.goto(`${web}/resources/gpu`);
    await page.getByText('管理节点注册', { exact: true }).click();
    await page.getByLabel('节点 ID', { exact: true }).fill('browser-node');
    await page.getByRole('button', { name: '签发注册码' }).click();
    const code = page.getByLabel('一次性注册码');
    await expect(code).toBeVisible();
    const registration = await http.post('/api/v1/nodes/register', { data: { code: await code.inputValue() } });
    expect(registration.status(), await registration.text()).toBe(201);
    const { token } = await registration.json();
    expect((await http.post('/api/v1/nodes/register', { data: { code: await code.inputValue() } })).status()).toBe(403);
    await page.getByRole('button', { name: '隐藏注册码' }).click();

    const { stdout } = await execute(python, ['-c', [
      'import json', 'from tests.fixtures.training_task import training_plan',
      'from apps.node.mjlab_launcher import compiler_hash',
      'print(json.dumps([training_plan("task/microduck-stand-up@1", algorithm={"library":"rsl_rl","name":"PPO","version":"5.0.1"}, evaluation={"scene":"microduck-arena@1","episodes":4,"threshold":4}), compiler_hash()]))',
    ].join('\n')], { cwd: root, env });
    const [document, compilerHash] = JSON.parse(stdout);
    const submitted = await http.post('/api/v1/plans', { headers: operator, data: document });
    expect(submitted.status(), await submitted.text()).toBe(201);
    const plan = await submitted.json();
    const validation = await http.post('/api/v1/jobs', { headers: operator, data: {
      kind: 'plan_validation', planId: plan.id, planRevision: 1, seed: 42, targetNodeId: 'browser-node',
    } });
    expect(validation.status(), await validation.text()).toBe(202);
    const { jobId } = await validation.json();
    await expect(page.getByRole('table', { name: '资源等待队列' })).toContainText(jobId);

    const headers = { 'X-Node-Id': 'browser-node', 'X-Node-Instance': 'browser-instance', 'X-Node-Token': token };
    const node = { instanceId: 'browser-instance', hardware: 'Browser fixture', compilerHash,
      trainingEntries: { mjlab: 'mjlab:managed' },
      trainerVersions: { mjlab: '1.3.0', rsl_rl: '5.0.1', mujoco_warp: '3.8.1', torch: '2.9.1', mujoco: '4.0.1', warp: '1.12.0' },
      gpus: [{ id: 'GPU-browser', name: 'Browser fixture GPU', memoryTotalMb: 8192,
        memoryUsedMb: 128, utilization: 22, observedAt: Date.now() / 1000 }] };
    const beat = (reports: object[] = []) => http.post('/api/v1/nodes/browser-node/heartbeat', {
      headers, data: { ...node, reports },
    });
    const dispatch = await beat();
    expect(dispatch.ok(), await dispatch.text()).toBe(true);
    const [assignment] = (await dispatch.json()).assignments;
    expect(assignment.jobId).toBe(jobId);
    const card = page.getByTestId('compute-node-browser-node');
    await expect(card).toContainText('已连接');
    await expect(card).toContainText('22%');
    const secondCode = await http.post('/api/v1/nodes/registrations', { headers: operator, data: { nodeId: 'browser-peer' } });
    expect(secondCode.status()).toBe(201);
    const second = await http.post('/api/v1/nodes/register', { data: { code: (await secondCode.json()).code } });
    expect(second.status()).toBe(201);
    const peer = await http.post('/api/v1/nodes/browser-peer/heartbeat', {
      headers: { 'X-Node-Id': 'browser-peer', 'X-Node-Instance': 'peer-instance', 'X-Node-Token': (await second.json()).token },
      data: { instanceId: 'peer-instance', hardware: 'CPU-only browser peer', gpus: [], cpuAvailable: true },
    });
    expect(peer.ok(), await peer.text()).toBe(true);
    await expect(page.getByTestId('compute-node-browser-peer')).toContainText('支持 CPU 作业');
    await observer.goto(`${web}/resources/gpu`);
    await expect(observer.getByTestId('compute-node-browser-node')).toBeVisible();
    await expect(observer.getByText('管理节点注册', { exact: true })).toHaveCount(0);
    expect((await http.post('/api/v1/nodes/registrations', { headers: viewer, data: {} })).status()).toBe(403);

    await page.goto(`${web}/approvals`);
    const approval = page.getByTestId(`approval-${plan.approvalId}`);
    await expect(approval).toContainText('当前修订暂无实测估算');
    const accepted = await beat([{ jobId, leaseId: assignment.leaseId, status: 'succeeded', iteration: 3,
      elapsedSeconds: 10, validation: { numEnvs: 4, setupSeconds: 5, warmupSeconds: 1, samplesSeconds: [.75, 1], peakMemoryMb: 128 } }]);
    expect(accepted.ok(), await accepted.text()).toBe(true);
    await expect(approval).toContainText('0.00479167 GPU 小时 · 17.25 秒');
    await expect(approval.getByRole('link', { name: '查看预检作业 →' })).toHaveAttribute('href', `/train/jobs/${jobId}`);
    await expect(approval).toContainText('待审批');

    const exportPath = '/api/v1/policies/microduck-walk-to/1/export';
    const expected = await http.get(exportPath, { headers: operator });
    expect(expected.ok(), await expected.text()).toBe(true);
    await page.goto(`${web}/resources/robots`);
    const robot = page.locator('[data-embodiment="microduck-sim"]');
    const [download] = await Promise.all([page.waitForEvent('download'), robot.getByRole('button', { name: '下载 Policy Export' }).click()]);
    expect(download.suggestedFilename()).toBe('microduck-walk-to-1.zip');
    expect(await readFile((await download.path())!)).toEqual(await expected.body());
    await observer.goto(`${web}/resources/robots`);
    await expect(observer.locator('[data-embodiment="microduck-sim"]')).toBeVisible();
    await expect(observer.getByRole('button', { name: '下载 Policy Export' })).toHaveCount(0);
    expect((await http.get(exportPath, { headers: viewer })).status()).toBe(403);

    await page.goto(`${web}/resources/gpu`);
    await page.getByText('管理节点注册', { exact: true }).click();
    await page.getByLabel('要吊销的节点').selectOption('browser-node');
    await page.getByRole('button', { name: '吊销节点令牌' }).click();
    await expect(page.getByTestId('compute-node-browser-node')).toHaveCount(0);
    await expect(page.getByTestId('compute-node-browser-peer')).toBeVisible();
    expect((await beat()).status()).toBe(401);
  } catch (error) { original.push(error); }
  finally {
    const report = await cleanupOwnedProcesses(children, { python, script, outputPath: name => info.outputPath(name),
      ports: [apiPort, webPort], original, context: { gateway, web }, prepare: [
        { label: 'close operator browser', run: () => browserOperator.close() },
        { label: 'close viewer browser', run: () => browserViewer.close() },
        { label: 'close HTTP client', run: () => http.dispose() },
      ] });
    finishCleanup(report, original);
  }
});
