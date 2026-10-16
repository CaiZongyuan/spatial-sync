import { writeFile } from 'node:fs/promises';
import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import type {} from '../../apps/web/src/components/Workbench';
import type { SceneManifest } from '../../packages/protocol/types';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const metrics = (page: Page) =>
  page.evaluate(() => window.__spatialDebug?.getSubscriptionMetrics());

for (const format of ['json', 'binary'] as const) {
  test(`real ${format} scoped deltas survive loss, rapid scope barriers, camera changes and reconnect`, async ({
    page,
    request,
  }, info) => {
    const created = await request.post(`${api}/api/v1/sessions`, {
      data: { sceneId: 'lab-demo' },
    });
    expect(created.ok()).toBe(true);
    const { sessionId } = await created.json();
    const route = `${api}/api/v1/sessions/${sessionId}`;
    const manifest = (await (
      await request.get(`${route}/scene`)
    ).json()) as SceneManifest;
    const errors: string[] = [],
      sockets: WebSocketRoute[] = [];
    const baselines = new Map<string, Map<number, number[]>>();
    const reconstructed = new Map<number, Map<number, number[]>>();
    let receivedDeltas = 0,
      droppedDeltas = 0,
      snapshots = 0,
      viewportRequests = 0;
    let dropKeyframe = false,
      droppedKeyframe = false,
      block = false,
      jsonPoseFrames = 0;
    page.on('pageerror', (error) => errors.push(error.message));
    await page.routeWebSocket('**/api/v1/sessions/*/ws', (socket) => {
      if (block)
        return socket.close({ code: 4001, reason: 'Scoped recovery check' });
      sockets.push(socket);
      const upstream = socket.connectToServer();
      socket.onMessage((payload) => {
        const value = JSON.parse(payload.toString());
        if (value.type === 'client.subscribe' && value.scope === 'viewport')
          viewportRequests++;
        upstream.send(payload);
      });
      upstream.onMessage((payload) => {
        let frame:
          | {
              type: string;
              seq: number;
              subscriptionId: number;
              baseSeq?: number;
              poses: number[][];
              removedStreamIds?: number[];
            }
          | undefined;
        if (typeof payload === 'string') {
          const value = JSON.parse(payload);
          if (
            ['transform.frame', 'transform.delta', 'stream.snapshot'].includes(
              value.type,
            )
          ) {
            frame = value;
            jsonPoseFrames++;
          }
        } else {
          const bytes = Buffer.from(payload);
          const kind = bytes.readUInt16LE(6),
            count = bytes.readUInt32LE(40);
          const offset = kind === 3 ? 60 : 48;
          frame = {
            type:
              kind === 3
                ? 'transform.delta'
                : kind === 5
                  ? 'stream.snapshot'
                  : 'transform.frame',
            seq: Number(bytes.readBigUInt64LE(16)),
            subscriptionId: bytes.readUInt32LE(44),
            poses: Array.from({ length: count }, (_, i) => [
              bytes.readUInt32LE(offset + i * 32),
              ...Array.from({ length: 7 }, (_, j) =>
                bytes.readFloatLE(offset + i * 32 + 4 + j * 4),
              ),
            ]),
          };
          if (kind === 3) {
            frame.baseSeq = Number(bytes.readBigUInt64LE(48));
            frame.removedStreamIds = Array.from(
              { length: bytes.readUInt32LE(56) },
              (_, i) => bytes.readUInt32LE(offset + count * 32 + i * 4),
            );
          }
        }
        if (frame) {
          if (frame.type === 'stream.snapshot') snapshots++;
          const key = `${frame.subscriptionId}:${frame.type === 'transform.delta' ? frame.baseSeq : frame.seq}`;
          let poses: Map<number, number[]>;
          if (frame.type === 'transform.delta') {
            receivedDeltas++;
            poses = new Map(baselines.get(key));
            for (const id of frame.removedStreamIds ?? []) poses.delete(id);
            for (const row of frame.poses) poses.set(row[0], row);
          } else {
            poses = new Map(frame.poses.map((row) => [row[0], row]));
            baselines.set(key, poses);
          }
          reconstructed.set(frame.seq, poses);
          if (
            frame.type === 'transform.delta' &&
            receivedDeltas <= 8 &&
            receivedDeltas % 2 === 0
          ) {
            droppedDeltas++;
            return;
          }
          // A deliberately lost full packet forces the declared-base recovery path.
          if (dropKeyframe && frame.type === 'transform.frame') {
            dropKeyframe = false;
            droppedKeyframe = true;
            return;
          }
        }
        socket.send(payload);
      });
    });
    try {
      await page.goto(`/sessions/${sessionId}?format=${format}`);
      await expect(page.getByTestId('connection-status')).toHaveText(
        'connected',
      );
      await expect
        .poll(async () => (await metrics(page))?.memberIds.length)
        .toBe(24);
      await expect.poll(() => droppedDeltas).toBeGreaterThan(1);
      const actual = await page.evaluate(
        (nodes) => ({
          sample: window.__spatialDebug!.getSample()!,
          poses: nodes.map((node) => ({
            id: node.streamId,
            pose: window.__spatialDebug!.getAuthoritativePose(node.entityKey),
          })),
        }),
        manifest.nodes.filter((node) => node.kind === 'body'),
      );
      const expected = reconstructed.get(actual.sample.seq)!;
      for (const { id, pose } of actual.poses) {
        expect(pose).toBeTruthy();
        for (let axis = 0; axis < 3; axis++)
          expect(pose!.p[axis]).toBeCloseTo(expected.get(id)![axis + 1], 5);
      }
      await page.getByRole('button', { name: 'Pause simulation' }).click();
      await expect(page.getByTestId('session-state')).toHaveText('PAUSED');
      const tube = '/World/Lab/Tubes/Tube01';
      await page.getByTestId(`entity-${tube}`).click();
      await expect(page.getByTestId('world-x')).toHaveText('0.800000');
      await page.getByLabel('Stream scope').selectOption('device');
      await page
        .getByLabel('Subscription group')
        .selectOption('/World/Lab/Robot');
      await expect
        .poll(async () => (await metrics(page))?.memberIds)
        .toEqual([1, 2]);
      await expect(page.getByTestId('pose-unavailable')).toBeVisible();
      await expect(page.getByTestId('world-x')).toHaveCount(0);
      expect(
        await page.evaluate(
          (key) => window.__spatialDebug!.getAuthoritativePose(key),
          tube,
        ),
      ).toBeUndefined();
      await page.screenshot({
        path: info.outputPath(`${format}-excluded-inspector.png`),
      });
      await page.getByLabel('Stream scope').selectOption('entities');
      await expect
        .poll(async () => (await metrics(page))?.memberIds)
        .toEqual([10]);
      await expect(page.getByTestId('world-x')).toHaveText('0.800000');
      // Three requests are submitted in one JS turn; only the last ack/snapshot opens the barrier.
      await page.evaluate(() => {
        const debug = window.__spatialDebug!;
        debug.setSubscription({
          scope: 'entities',
          entityKeys: ['/World/Lab/Robot/Link1'],
          delta: true,
        });
        debug.setSubscription({ scope: 'all', delta: true });
        debug.setSubscription({
          scope: 'device',
          deviceKey: '/World/Lab/Tubes',
          delta: true,
        });
      });
      await expect
        .poll(async () => (await metrics(page))?.memberIds.length)
        .toBe(20);
      await page.getByLabel('Stream scope').selectOption('viewport');
      await expect.poll(() => viewportRequests).toBeGreaterThan(0);
      await expect
        .poll(async () => (await metrics(page))?.resyncPending)
        .toBe(false);
      const beforeCamera = viewportRequests;
      const canvas = await page.locator('canvas').boundingBox();
      expect(canvas).toBeTruthy();
      await page.mouse.move(
        canvas!.x + canvas!.width / 2,
        canvas!.y + canvas!.height / 2,
      );
      await page.mouse.down();
      await page.mouse.move(
        canvas!.x + canvas!.width / 2 + 200,
        canvas!.y + canvas!.height / 2 + 40,
        { steps: 20 },
      );
      await page.mouse.up();
      await expect.poll(() => viewportRequests).toBeGreaterThan(beforeCamera);
      // OrbitControls continues moving during damping after pointer release.
      // Wait for an actually unchanged camera rather than assuming a fixed delay.
      await expect
        .poll(
          async () => {
            const stationary = viewportRequests;
            await page.waitForTimeout(400);
            return viewportRequests - stationary;
          },
          { timeout: 15000 },
        )
        .toBe(0);
      // Explicit protocol physics-world planes are exercised through the actual client/WS API.
      await page.evaluate(() =>
        window.__spatialDebug!.setSubscription({
          scope: 'viewport',
          delta: true,
          viewport: {
            planes: [
              [1, 0, 0, -100],
              [-1, 0, 0, 101],
              [0, 1, 0, 1],
              [0, -1, 0, 1],
              [0, 0, 1, 1],
              [0, 0, -1, 1],
            ],
          },
        }),
      );
      await expect
        .poll(async () => (await metrics(page))?.memberIds)
        .toEqual([31]);
      await expect(page.getByTestId('pose-unavailable')).toBeVisible();
      await page.getByRole('button', { name: 'Resume simulation' }).click();
      await expect(page.getByTestId('session-state')).toHaveText('RUNNING');
      dropKeyframe = true;
      await expect.poll(() => droppedKeyframe).toBe(true);
      await expect.poll(async () => (await metrics(page))?.resyncCount).toBe(1);
      await expect
        .poll(async () => (await metrics(page))?.resyncPending)
        .toBe(false);
      const beforeReconnect = snapshots;
      block = true;
      await sockets
        .at(-1)!
        .close({ code: 4001, reason: 'Actual upstream connection loss' });
      await expect(page.getByTestId('connection-status')).not.toHaveText(
        'connected',
      );
      block = false;
      await expect(page.getByTestId('connection-status')).toHaveText(
        'connected',
      );
      await expect.poll(() => snapshots).toBeGreaterThan(beforeReconnect);
      await expect
        .poll(async () => (await metrics(page))?.memberIds)
        .toEqual([31]);
      if (format === 'binary') expect(jsonPoseFrames).toBe(0);
      expect(receivedDeltas).toBeGreaterThan(8);
      expect(errors).toEqual([]);
      const wireEvidence = JSON.stringify(
        {
          format,
          receivedDeltas,
          droppedDeltas,
          droppedKeyframe,
          viewportRequests,
          snapshots,
          jsonPoseFrames,
          metrics: await metrics(page),
          oracleSeq: actual.sample.seq,
        },
        null,
        2,
      );
      const evidencePath = info.outputPath('scope-wire-evidence.json');
      await writeFile(evidencePath, wireEvidence);
      await info.attach('scope-wire-evidence.json', {
        path: evidencePath,
        contentType: 'application/json',
      });
    } finally {
      await request.delete(route);
    }
  });
}
