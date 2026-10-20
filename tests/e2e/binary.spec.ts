import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import type {} from '../../apps/web/src/components/Workbench';
import type { SceneManifest, Snapshot } from '../../packages/protocol/types';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const sample = (page: Page) =>
  page.evaluate(() => window.__spatialDebug?.getSample());
const metrics = (page: Page) =>
  page.evaluate(() => window.__spatialDebug?.getStreamMetrics());

async function connected(page: Page) {
  await expect(page.getByTestId('connection-status')).toHaveText('connected');
  await expect
    .poll(async () => (await sample(page))?.seq ?? -1)
    .toBeGreaterThan(0);
  await expect(page.locator('canvas')).toBeVisible();
}

test('binary browsers share authoritative controls, recovery and replacement', async ({
  page,
  browser,
  request,
}, testInfo) => {
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneId: 'lab-demo', source: 'synthetic' },
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  const route = `${api}/api/v1/sessions/${sessionId}`;
  const observerContext = await browser.newContext();
  const observer = await observerContext.newPage();
  const errors: string[] = [];
  let binaryFrames = 0,
    observerBinaryFrames = 0,
    jsonFrames = 0,
    jsonCommands = 0,
    block = false;
  const sockets: WebSocketRoute[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('websocket', (socket) => {
    socket.on('framereceived', ({ payload }) => {
      if (Buffer.isBuffer(payload)) {
        expect(payload.subarray(0, 4).toString()).toBe('NSYN');
        binaryFrames++;
      } else if (['transform.frame', 'stream.snapshot'].includes(JSON.parse(payload).type))
        jsonFrames++;
    });
    socket.on('framesent', ({ payload }) => {
      expect(typeof payload).toBe('string');
      if (JSON.parse(payload as string).type === 'command.request')
        jsonCommands++;
    });
  });
  observer.on('websocket', (socket) =>
    socket.on('framereceived', ({ payload }) => {
      if (Buffer.isBuffer(payload)) {
        expect(payload.subarray(0, 4).toString()).toBe('NSYN');
        observerBinaryFrames++;
      } else if (['transform.frame', 'stream.snapshot'].includes(JSON.parse(payload).type))
        jsonFrames++;
    }),
  );
  await page.routeWebSocket('**/api/v1/sessions/*/ws', (socket) => {
    if (block)
      return socket.close({ code: 4001, reason: 'Recovery verification' });
    socket.connectToServer();
    sockets.push(socket);
  });
  try {
    await Promise.all([page.goto(`/sessions/${sessionId}`), observer.goto(`/sessions/${sessionId}`)]);
    await Promise.all([connected(page), connected(observer)]);
    await expect
      .poll(async () => (await metrics(page))?.frameFormat)
      .toBe('binary');
    expect((await metrics(observer))?.frameFormat).toBe('binary');
    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('PAUSED');
    await expect(observer.getByTestId('session-state')).toHaveText('PAUSED');
    const snapshot = (await (
      await request.get(`${route}/snapshot`)
    ).json()) as Snapshot;
    const manifest = (await (
      await request.get(`${route}/scene`)
    ).json()) as SceneManifest;
    for (const view of [page, observer]) {
      await expect
        .poll(async () => (await sample(view))?.seq)
        .toBe(snapshot.seq);
      const poses = await view.evaluate(
        (nodes) =>
          nodes.map((node) => ({
            id: node.streamId,
            pose: window.__spatialDebug?.getAuthoritativePose(node.entityKey),
          })),
        manifest.nodes
          .filter((node) => node.kind === 'body')
          .map((node) => ({
            streamId: node.streamId,
            entityKey: node.entityKey,
          })),
      );
      for (const row of snapshot.poses) {
        const pose = poses.find((value) => value.id === row[0])!.pose!;
        [...pose.p, ...pose.q].forEach((value, index) =>
          expect(Math.abs(value - row[index + 1])).toBeLessThan(1e-6),
        );
      }
    }
    // A paused reconnect must accept the same sequence as a recovery snapshot.
    const received = (await metrics(page))!.receivedFrames;
    block = true;
    await sockets
      .at(-1)!
      .close({ code: 4001, reason: 'Recovery verification' });
    await expect(page.getByTestId('connection-status')).not.toHaveText(
      'connected',
    );
    block = false;
    await connected(page);
    await expect
      .poll(async () => (await metrics(page))?.receivedFrames ?? 0)
      .toBeGreaterThan(received);
    expect((await sample(page))?.seq).toBe(snapshot.seq);
    const afterReconnect = (await metrics(page))!.receivedFrames;
    await page.evaluate(() =>
      document.dispatchEvent(new Event('visibilitychange')),
    );
    await expect
      .poll(async () => (await metrics(page))?.receivedFrames ?? 0)
      .toBeGreaterThan(afterReconnect);
    expect((await sample(page))?.seq).toBe(snapshot.seq);
    await page.getByRole('button', { name: 'Step simulation' }).click();
    await expect
      .poll(async () => (await sample(observer))?.seq ?? 0)
      .toBeGreaterThan(snapshot.seq);
    await expect.poll(async () => (await sample(page))?.simTime ?? 0).toBeGreaterThan(snapshot.simTime);
    const stepped = await sample(page);
    expect(stepped!.simTime).toBeGreaterThan(snapshot.simTime);
    await page.getByRole('button', { name: 'Reset simulation' }).click();
    await expect
      .poll(async () => (await sample(page))?.epoch)
      .toBe(snapshot.epoch + 1);
    await expect.poll(async () => (await sample(observer))?.simTime).toBe(0);
    const priorSeq = (await sample(page))!.seq;
    await page
      .getByRole('button', { name: 'Reload scene', exact: true })
      .click();
    await expect
      .poll(async () => (await metrics(page))?.revision)
      .not.toBe(manifest.sceneRevision);
    await expect
      .poll(async () => (await metrics(observer))?.revision)
      .not.toBe(manifest.sceneRevision);
    await expect
      .poll(async () => (await sample(page))?.seq ?? 0)
      .toBeGreaterThan(priorSeq);
    await expect
      .poll(async () => (await metrics(page))?.receivedFrames ?? 0)
      .toBeGreaterThan(afterReconnect + 2);
    expect((await metrics(page))?.invalidBinaryFrames).toBe(0);
    expect(binaryFrames).toBeGreaterThan(3);
    expect(observerBinaryFrames).toBeGreaterThan(3);
    expect(jsonFrames).toBe(0);
    expect(jsonCommands).toBe(3);
    expect(errors).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath('binary-workbench.png'),
    });
    await testInfo.attach('wire-and-client-receipt', {
      body: JSON.stringify(
        {
          binaryFrames,
          observerBinaryFrames,
          jsonFrames,
          jsonCommands,
          binary: await metrics(page),
          observer: await metrics(observer),
        },
        null,
        2,
      ),
      contentType: 'application/json',
    });
  } finally {
    await observerContext.close();
    await request.delete(route);
  }
});
