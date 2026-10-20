import { expect, test, type BrowserContext, type Page, type WebSocketRoute } from '@playwright/test';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import type {} from '../../apps/web/src/components/Workbench';
import type { SceneManifest, Snapshot } from '../../packages/protocol/types';
import { cleanupObserver } from './cleanup-observer';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const sample = (page: Page) =>
  page.evaluate(() => window.__spatialDebug?.getSample());
const metrics = (page: Page) =>
  page.evaluate(() => window.__spatialDebug?.getStreamMetrics());

async function connected(page: Page) {
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');
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
  let observerContext: BrowserContext | undefined;
  const failures: unknown[] = [];
  try {
    observerContext = await browser.newContext();
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
    await page.goto(`/sessions/${sessionId}`);
    await observer.goto(`/sessions/${sessionId}`);
    await connected(page);
    await connected(observer);
    await expect
      .poll(async () => (await metrics(page))?.frameFormat)
      .toBe('binary');
    expect((await metrics(observer))?.frameFormat).toBe('binary');
    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('已暂停');
    await expect(observer.getByTestId('session-state')).toHaveText('已暂停');
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
      '已连接',
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
  } catch (error) {
    failures.push(error);
  } finally {
    await cleanupObserver(observerContext, [route], failures);
  }
});

test('observer close failure stops only its owned session and preserves the original failure', async ({ request }) => {
  const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic', continueWithoutObservers: true } });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  const route = `${api}/api/v1/sessions/${sessionId}`;
  const ownedRoutes = [route];
  const failures: unknown[] = [];
  try {
    const original = new Error('Injected original journey failure');
    const close = new Error('Injected observer close failure');
    const context = { close: async () => { throw close; } };
    const closeFailure = await cleanupObserver(context, [route], [original]).catch(error => error);
    expect(closeFailure).toBeInstanceOf(AggregateError);
    expect(closeFailure.cause).toBe(original);
    expect(closeFailure.errors).toEqual([original, close]);
    expect(closeFailure.errors[0]).toBe(original);
    expect(closeFailure.errors[1]).toBe(close);
    expect((await (await request.get(route)).json()).state).toBe('STOPPED');
    const next = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo', source: 'synthetic', continueWithoutObservers: true } });
    expect(next.ok()).toBe(true);
    const nextRoute = `${api}/api/v1/sessions/${(await next.json()).sessionId}`;
    ownedRoutes.push(nextRoute);
    await expect(cleanupObserver(context, [route])).rejects.toBe(close);
    await expect(cleanupObserver(undefined, [route], [undefined])).rejects.toBeUndefined();
    expect((await (await request.get(nextRoute)).json()).state).toBe('RUNNING');
    let releaseClose!: () => void;
    const heldClose = new Promise<void>(resolve => { releaseClose = resolve; });
    let closeStarted = false, closeFinished = false;
    const stopping = cleanupObserver({ close: async () => {
      closeStarted = true; await heldClose; closeFinished = true;
    } }, [nextRoute]);
    try {
      await expect.poll(async () => (await (await request.get(nextRoute)).json()).state).toBe('STOPPED');
      expect(closeStarted).toBe(true);
      expect(closeFinished).toBe(false);
    } catch (error) { failures.push(error); }
    finally {
      releaseClose();
      await stopping.catch(error => { failures.push(error); });
    }
    expect(closeFinished).toBe(true);
    const deleted: string[] = [];
    const faults = createServer((request, response) => {
      deleted.push(`${request.method} ${request.url}`);
      if (request.url === '/reject') response.destroy();
      else response.writeHead(request.url === '/500' ? 500 : 200).end();
    });
    try {
      await new Promise<void>(resolve => faults.listen(0, '127.0.0.1', resolve));
      const origin = `http://127.0.0.1:${(faults.address() as AddressInfo).port}`;
      const deleteFailure = await cleanupObserver(undefined, ['/500', '/reject', '/after'].map(path => origin + path), [original]).catch(error => error);
      expect(deleteFailure).toBeInstanceOf(AggregateError);
      expect(deleteFailure.cause).toBe(original);
      expect(deleteFailure.errors[0]).toBe(original);
      expect(deleteFailure.errors[1].message).toBe('Owned session cleanup failed: HTTP 500');
      expect(deleteFailure.errors[2]).toBeInstanceOf(TypeError);
      expect(deleted).toEqual(['DELETE /500', 'DELETE /reject', 'DELETE /after']);
    } finally {
      faults.closeAllConnections();
      await new Promise<void>(resolve => faults.close(() => resolve()));
    }
  } catch (error) {
    failures.push(error);
  } finally {
    await cleanupObserver(undefined, ownedRoutes, failures);
  }
});
