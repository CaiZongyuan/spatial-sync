import { expect, test, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test';
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

interface RemoteWorkbench {
  http: APIRequestContext;
  web: string;
  operator: Record<string, string>;
  viewer: Record<string, string>;
  open: (headers: Record<string, string>) => Promise<Page>;
  planFixture: () => Promise<{ document: object; compilerHash: string }>;
}

const remoteTest = test.extend<{ remote: RemoteWorkbench }>({
  remote: async ({ browser, playwright }, use, info) => {
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
    const contexts = new Set<BrowserContext>();
    async function open(headers: Record<string, string>) {
      const context = await browser.newContext({ extraHTTPHeaders: headers });
      contexts.add(context);
      context.on('close', () => contexts.delete(context));
      return context.newPage();
    }
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

      async function planFixture() {
        const { stdout } = await execute(python, ['-c', [
          'import json', 'from tests.fixtures.training_task import training_plan',
          'from apps.node.mjlab_launcher import compiler_hash',
          'print(json.dumps([training_plan("task/microduck-stand-up@1", algorithm={"library":"rsl_rl","name":"PPO","version":"5.0.1"}, evaluation={"scene":"microduck-arena@1","episodes":4,"threshold":4}), compiler_hash()]))',
        ].join('\n')], { cwd: root, env });
        const [document, compilerHash] = JSON.parse(stdout);
        return { document, compilerHash };
      }
      await use({ http, web, operator, viewer, open, planFixture });
    } catch (error) { original.push(error); }
    finally {
      const report = await cleanupOwnedProcesses(children, { python, script, outputPath: name => info.outputPath(name),
        ports: [apiPort, webPort], original, context: { gateway, web }, prepare: [
          ...Array.from(contexts, context => ({ label: 'close browser context', run: () => context.close() })),
          { label: 'close HTTP client', run: () => http.dispose() },
        ] });
      finishCleanup(report, original);
    }
  },
});

remoteTest('registered node drives the pending approval estimate and the exact Policy Export', async ({ remote }) => {
  const { http, web, operator, open, planFixture } = remote;
  const { document, compilerHash } = await planFixture();
  const page = await open(operator);
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
  await page.getByRole('link', { name: 'Approvals', exact: true }).click();
  const approval = page.getByTestId(`approval-${plan.approvalId}`);
  await expect(approval).toContainText('当前修订暂无实测估算');
  const accepted = await beat([{ jobId, leaseId: assignment.leaseId, status: 'succeeded', iteration: 3,
    elapsedSeconds: 10, validation: { numEnvs: 4, setupSeconds: 5, warmupSeconds: 1, samplesSeconds: [.75, 1], peakMemoryMb: 128 } }]);
  expect(accepted.ok(), await accepted.text()).toBe(true);
  await expect(approval).toContainText('0.00479167 GPU 小时 · 17.25 秒');
  await expect(approval.getByRole('link', { name: '查看预检作业 →' })).toHaveAttribute('href', `/train/jobs/${jobId}`);
  await expect(approval).toContainText('待审批');

  await page.getByRole('link', { name: 'Resources', exact: true }).click();
  await page.getByText('管理节点注册', { exact: true }).click();
  await page.getByLabel('要吊销的节点').selectOption('browser-node');
  await page.getByRole('button', { name: '吊销节点令牌' }).click();
  await expect(page.getByTestId('compute-node-browser-node')).toHaveCount(0);
  await expect(page.getByTestId('compute-node-browser-peer')).toBeVisible();
  expect((await beat()).status()).toBe(401);
  await page.getByRole('navigation', { name: '资源标签', exact: true }).locator('a[href="/resources/robots"]').click();
  const robot = page.locator('[data-embodiment="microduck-sim"]');
  const [download] = await Promise.all([
    page.waitForEvent('download'), robot.getByRole('button', { name: '下载 Policy Export' }).click(),
  ]);
  expect(download.suggestedFilename()).toBe('microduck-walk-to-1.zip');
  const downloaded = await readFile((await download.path())!);
  await page.context().close();
  const expected = await http.get('/api/v1/policies/microduck-walk-to/1/export', { headers: operator });
  expect(expected.ok(), await expected.text()).toBe(true);
  expect(downloaded).toEqual(await expected.body());
});

remoteTest('viewer reads node and robot resources with operator actions withheld', async ({ remote }) => {
  const { http, web, operator, viewer, open } = remote;
  const issued = await http.post('/api/v1/nodes/registrations', { headers: operator, data: { nodeId: 'viewer-node' } });
  expect(issued.status()).toBe(201);
  const registered = await http.post('/api/v1/nodes/register', { data: { code: (await issued.json()).code } });
  expect(registered.status()).toBe(201);
  const beat = await http.post('/api/v1/nodes/viewer-node/heartbeat', {
    headers: { 'X-Node-Id': 'viewer-node', 'X-Node-Instance': 'viewer-instance', 'X-Node-Token': (await registered.json()).token },
    data: { instanceId: 'viewer-instance', hardware: 'Viewer resource fixture', gpus: [], cpuAvailable: true },
  });
  expect(beat.ok(), await beat.text()).toBe(true);
  const page = await open(viewer);
  const permissions = page.waitForResponse(response => response.url().includes('/api/v1/approvals?view=summary'));
  await page.goto(`${web}/resources/gpu`);
  expect((await (await permissions).json()).role).toBe('viewer');
  await expect(page.getByTestId('compute-node-viewer-node')).toBeVisible();
  await expect(page.getByText('管理节点注册', { exact: true })).toHaveCount(0);
  await page.getByRole('navigation', { name: '资源标签', exact: true }).locator('a[href="/resources/robots"]').click();
  await expect(page.locator('[data-embodiment="microduck-sim"]')).toBeVisible();
  await expect(page.getByRole('button', { name: '下载 Policy Export' })).toHaveCount(0);
  await page.context().close();
  expect((await http.post('/api/v1/nodes/registrations', { headers: viewer, data: {} })).status()).toBe(403);
  expect((await http.get('/api/v1/policies/microduck-walk-to/1/export', { headers: viewer })).status()).toBe(403);
});

remoteTest('video drop binds a CPU result and reviewed ghost to one robot revision', async ({ remote }, info) => {
  const { http, web, operator, viewer, open } = remote;
  const output = info.outputPath('source.mp4');
  await promisify(execFile)('ffmpeg', ['-v', 'error', '-threads', '1', '-f', 'lavfi', '-i', 'color=s=64x64:r=2',
    '-t', '2', '-c:v', 'libx264', '-threads', '1', output], { timeout: 10_000 });
  const video = await readFile(output);
  const nodeId = 'browser-pose';
  const issued = await http.post('/api/v1/nodes/registrations', { headers: operator, data: { nodeId } });
  expect(issued.status()).toBe(201);
  const joined = await http.post('/api/v1/nodes/register', { data: { code: (await issued.json()).code } });
  expect(joined.status()).toBe(201);
  const node = { 'X-Node-Id': nodeId, 'X-Node-Instance': 'pose-browser-fixture', 'X-Node-Token': (await joined.json()).token };
  const beat = (reports: object[] = []) => http.post(`/api/v1/nodes/${nodeId}/heartbeat`, { headers: node, data: {
    instanceId: node['X-Node-Instance'], hardware: 'Synthetic browser pose fixture', cpuAvailable: true,
    gpus: [], poseRuntime: 'mediapipe-pose-landmarker-lite/1', reports,
  } });
  const asset = await (await http.get('/api/v1/library/assets/robot/microduck/1', { headers: operator })).json();

  const page = await open(operator);
  await page.goto(`${web}/assets/robot/microduck/1`);
  await page.getByText('视频提取与重定向', { exact: true }).click();
  const transfer = await page.evaluateHandle(bytes => {
    const files = new DataTransfer();
    files.items.add(new File([new Uint8Array(bytes)], 'source.mp4', { type: 'video/mp4' }));
    return files;
  }, [...video]);
  await page.getByRole('group', { name: '上传动作视频' }).dispatchEvent('drop', { dataTransfer: transfer });
  await page.getByLabel('视频许可证').fill('CC0-1.0');
  await page.getByLabel('署名', { exact: true }).fill('Synthetic browser fixture');
  const awaitingVideo = page.waitForResponse(reply => reply.request().method() === 'POST'
    && new URL(reply.url()).pathname === '/api/v1/library/motion-videos');
  await page.getByRole('button', { name: '上传视频' }).click();
  const uploaded = await awaitingVideo;
  expect(uploaded.status(), await uploaded.text()).toBe(201);
  const source = await uploaded.json();
  const awaitingJob = page.waitForResponse(reply => reply.request().method() === 'POST'
    && new URL(reply.url()).pathname === '/api/v1/jobs');
  await page.getByRole('button', { name: '提交 CPU 提取' }).click();
  const queued = await awaitingJob;
  expect(queued.status(), await queued.text()).toBe(202);
  const job = await queued.json();
  expect(job).toMatchObject({ motionVideo: source.id, executionDevice: 'cpu', status: 'queued' });
  const dispatch = await beat();
  expect(dispatch.ok(), await dispatch.text()).toBe(true);
  const [assignment] = (await dispatch.json()).assignments;
  expect(assignment).toMatchObject({ jobId: job.jobId, resourceId: 'cpu', timeoutSeconds: 180 });
  const status = { jobId: job.jobId, leaseId: assignment.leaseId };
  expect((await beat([{ ...status, status: 'running' }])).status()).toBe(200);
  await expect(page.getByText(/CPU 提取中.*browser-pose/)).toBeVisible();

  // Deterministic CPU Node wire fixture. The live Gateway validates the video lease and publishes the clip.
  const pose = (index: number) => Array.from({ length: 16 }, (_, joint) => ({
    position: ({ 6: [0, .1, .8], 7: [0, -.1, .8], 8: [0, .1, .4], 9: [0, -.1, .4],
      10: [.08 * index, .1, .05], 11: [.08 * index, -.1, .05] } as Record<number, number[]>)[joint]
      ?? [joint / 100, 0, .1],
    inFrame: true, visibility: .9,
  }));
  const published = await http.post(`/api/v1/jobs/${job.jobId}/artifacts`, {
    headers: { ...node, 'X-Lease-Id': assignment.leaseId },
    data: {
      sourceSha256: source.sha256,
      modelSha256: '59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a',
      frames: source.frameTimes.map((timeSeconds: number, index: number) => ({ timeSeconds, poses: [pose(index)] })),
    },
  });
  expect(published.ok(), await published.text()).toBe(true);
  const { motionClip } = await published.json();
  const clipPath = `/api/v1/library/motion-clips/${motionClip.sha256}/1`;
  const clipReads: string[] = [];
  const replayWrites: string[] = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/v1/library/motion-clips/')) clipReads.push(path);
    if (request.method() === 'POST' && (path === '/api/v1/jobs' || path === '/api/v1/library/motion-videos')) {
      replayWrites.push(path);
    }
  });
  const failures = [429, 503];
  await page.route(`**${clipPath}`, async route => {
    const status = failures.shift();
    if (status) await route.fulfill({ status, json: { detail: { message: `Clip read ${status}` } } });
    else await route.continue();
  });
  expect((await beat([{ ...status, status: 'succeeded' }])).status()).toBe(200);
  await expect(page.getByText('Clip read 429', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '重试读取片段' }).click();
  await expect(page.getByText('Clip read 503', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '重试读取片段' }).click();
  await expect(page.getByText(motionClip.id, { exact: true })).toBeVisible();
  expect(clipReads).toEqual([clipPath, clipPath, clipPath]);
  expect(replayWrites).toEqual([]);
  await expect(page.getByRole('link', { name: '查看提取作业' })).toHaveAttribute('href', `/train/jobs/${job.jobId}`);
  const mapping = {
    pelvis: '/Asset/trunk_base', trunk: '/Asset/trunk_base', head: '/Asset/jaw_soft',
    leftFoot: '/Asset/ankle_left/left_foot', rightFoot: '/Asset/ankle_right/right_foot',
    bends: {
      leftLeg: { joint: '/Asset/joints/left_knee', gain: .1 },
      rightLeg: { joint: '/Asset/joints/right_knee', gain: -.1 },
    },
    auxiliaryJoints: ['left', 'right'].flatMap(side =>
      ['hip_yaw', 'hip_roll', 'hip_pitch', 'ankle'].map(joint => `/Asset/joints/${side}_${joint}`)),
  };
  await page.getByLabel('肢体映射 JSON').fill(JSON.stringify(mapping));
  await expect(page.getByRole('checkbox', { name: '原地模式' })).toHaveCount(0);
  await expect(page.getByText(/世界平移轨迹.*关键帧/)).toBeVisible();
  const model = page.getByLabel('素材静态模型预览');
  const canvas = model.locator('canvas');
  await expect(model).toHaveAttribute('data-model-state', 'ready');
  const originalCanvas = await canvas.elementHandle();
  const awaitingReference = page.waitForResponse(reply => reply.request().method() === 'POST'
    && new URL(reply.url()).pathname === '/api/v1/library/reference-motions');
  await page.getByRole('button', { name: '生成参考动作' }).click();
  const fitted = await awaitingReference;
  expect(fitted.status(), await fitted.text()).toBe(201);
  expect(fitted.request().postDataJSON()).toMatchObject({ inPlace: true });
  const reference = await fitted.json();
  expect(reference.motion).toMatchObject({ asset: asset.id, assetContentHash: asset.contentHash });
  expect(reference.source).toMatchObject({ clip: { id: motionClip.id, sha256: motionClip.sha256 }, video: { id: source.id } });
  await expect(page.getByLabel('参考动作版本')).toHaveValue(reference.id);
  await expect(model).toHaveAttribute('data-motion-overlay', 'visible');
  expect(await canvas.evaluate((current, original) => current === original, originalCanvas)).toBe(true);
  expect(Number(await model.getAttribute('data-mesh-count'))).toBeGreaterThan(0);
  const firstFrame = await canvas.screenshot();
  const phase = page.getByRole('slider', { name: '相位时间' });
  await phase.press('End');
  await expect(phase).toHaveValue(String(reference.ghost.samples.at(-1).time));
  await expect.poll(async () => (await canvas.screenshot()).equals(firstFrame)).toBe(false);
  await expect(page.getByText('关键点误差', { exact: true })).toBeVisible();
  await expect(page.getByText('速度与上限', { exact: true })).toBeVisible();
  const feasibility = page.getByRole('region', { name: '参考动作可行性' });
  const reportValue = (label: string) => feasibility.getByText(label, { exact: true }).locator('..').locator('dd');
  await expect(reportValue('根线速度 / 上限')).toHaveText(
    `${reference.report.maxRootSpeedMps} m/s / ${reference.report.bounds.rootSpeedMps} m/s`,
  );
  await expect(reportValue('旋转关节速度上限')).toHaveText(`${reference.report.bounds.jointSpeedRadps} rad/s`);
  await expect(reportValue('移动关节速度上限')).toHaveText(`${reference.report.bounds.slideSpeedMps} m/s`);
  for (const [name, value] of Object.entries(reference.report.keypointErrorM)) {
    const row = feasibility.getByRole('row', { name: new RegExp(name) });
    await expect(row).toContainText(`${value} m`);
    const target = reference.report.keypointTargets?.[name];
    if (target) await expect(row).toContainText(target.path);
  }
  await page.getByText('从参考动作创建 Task', { exact: true }).click();
  const submit = page.getByRole('button', { name: '创建固定版本 Task' });
  await expect(submit).toBeDisabled();
  const root = resolve('../..'), python = process.env.SPATIAL_PYTHON ?? resolve(root, '.venv/bin/python');
  const { stdout } = await promisify(execFile)(python, ['-c', [
    'import json, sys',
    'from tests.gateway.test_reference_motion import imitation_request',
    'print(json.dumps(imitation_request({"id": sys.argv[1], "sha256": sys.argv[2]})))',
  ].join('\n'), reference.id, reference.sha256], { cwd: root });
  const taskInput = JSON.parse(stdout);
  taskInput.reference = { id: 'reference-motion/' + '0'.repeat(64) + '@1', sha256: '0'.repeat(64) };
  await page.getByLabel('Task 和观测 JSON').setInputFiles({
    name: 'imitation-task.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(taskInput)),
  });
  await expect(submit).toBeDisabled();
  await page.getByLabel('已检查此版本的动作预览').check();
  const awaitingTask = page.waitForResponse(reply => reply.request().method() === 'POST'
    && new URL(reply.url()).pathname === '/api/v1/tasks/reference-motion');
  await submit.click();
  const assembled = await awaitingTask;
  expect(assembled.status(), await assembled.text()).toBe(201);
  const task = await assembled.json();
  expect(task.task.referenceMotionRef).toEqual({ id: reference.id, sha256: reference.sha256 });
  await expect(page.getByText(task.id, { exact: true })).toBeVisible();
  await page.context().close();

  const readonly = await open(viewer);
  const permissions = readonly.waitForResponse(reply => reply.url().includes('/api/v1/approvals?view=summary'));
  await readonly.goto(`${web}/assets/robot/microduck/1`);
  expect((await (await permissions).json()).role).toBe('viewer');
  await readonly.getByLabel('参考动作版本').selectOption(reference.id);
  await expect(readonly.getByLabel('素材静态模型预览')).toHaveAttribute('data-motion-overlay', 'visible');
  await expect(readonly.getByText('视频提取与重定向', { exact: true })).toHaveCount(0);
  await expect(readonly.getByText('从参考动作创建 Task', { exact: true })).toHaveCount(0);
});
