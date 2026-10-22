import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import type {} from '../../apps/web/src/components/Workbench';
import type { CameraState } from '../../apps/web/src/components/viewer/SceneDiagnostics';

const fixture = resolve('../../tests/fixtures/recordings/v1');
const outlierKey = '/World/TestFramingBody';
const camera = (page: Page) => page.evaluate(() => window.__spatialRenderDebug?.camera());
const distance = (a: number[], b: number[]) => Math.hypot(...a.map((value, index) => value - b[index]));

/** Real renderer + recorded binary sample, without creating a Session or Source. */
async function viewerFixture(page: Page, delayVisual = false) {
  const manifest = JSON.parse(readFileSync(join(fixture, 'manifest.json'), 'utf8'));
  const catalog = JSON.parse(readFileSync(join(fixture, 'catalog.json'), 'utf8'));
  const primitive = manifest.nodes.find((node: { kind: string; visuals: { kind: string; geometry: { type: string } }[] }) =>
    node.kind === 'body' && node.visuals.length && node.visuals.every(visual => visual.kind === 'primitive' && visual.geometry.type !== 'plane'));
  const id = Math.max(...manifest.nodes.filter((node: { kind: string }) => node.kind === 'body')
    .map((node: { streamId: number }) => node.streamId)) + 1;
  manifest.nodes.push({ ...primitive, entityKey: outlierKey, name: 'Framing fixture body', streamId: id });
  const segment = readFileSync(join(fixture, 'seg-000001.bin'));
  const original = segment.subarray(19, 10 + segment.readUInt32LE(6));
  const spatial = Buffer.alloc(original.length + 32);
  original.copy(spatial);
  const count = original.readUInt32LE(40);
  spatial.writeUInt32LE(count + 1, 40);
  const offset = 44 + count * 32;
  spatial.writeUInt32LE(id, offset);
  [1, 0, 1, 0, 0, 0, 1].forEach((value, index) => spatial.writeFloatLE(value, offset + 4 + index * 4));
  let socket: WebSocketRoute | undefined;
  let sequence = Number(spatial.readBigUInt64LE(16));
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/sessions/framing-fixture/scene', route => route.fulfill({ json: manifest }));
  await page.route('**/api/v1/sessions/framing-fixture/catalog', route => route.fulfill({ json: catalog }));
  await page.route('**/api/v1/sessions/framing-fixture/stats', route => route.fulfill({ json: {} }));
  if (delayVisual) await page.route('**/*missing-reference*', async route => {
    await held; await route.fulfill({ status: 404 });
  });
  await page.routeWebSocket('**/api/v1/sessions/framing-fixture/ws', route => {
    socket = route;
    route.onMessage(message => {
      const request = JSON.parse(String(message));
      if (request.type === 'client.subscribe') {
        route.send(JSON.stringify({ type: 'subscription.ack', requestId: request.requestId, grantedRateHz: 30 }));
        route.send(spatial);
      }
    });
    route.send(JSON.stringify({ type: 'server.ready', sessionId: 'framing-fixture', role: 'viewer',
      sceneRevision: manifest.sceneRevision, sessionState: 'PAUSED', epoch: 0, seq: sequence,
      simTime: 0, realtimeFactor: 1, heartbeatIntervalMs: 30000 }));
  });
  return { release, remotePose() {
    const next = Buffer.from(spatial);
    next.writeBigUInt64LE(BigInt(++sequence), 16);
    next.writeFloatLE(1e9, offset + 4);
    socket!.send(next);
  } };
}

async function assertViewRetained(page: Page, before: CameraState) {
  await expect.poll(async () => {
    const after = await camera(page);
    return after && distance(after.position, before.position) < .02 && distance(after.target, before.target) < .001
      && Math.abs((after.zoom ?? 1) - (before.zoom ?? 1)) < .001;
  }).toBe(true);
}

for (const render of ['individual', 'instanced']) test(`${render} view survives a remote recorded body and resize, with explicit recovery`, async ({ page }, info) => {
  const fixture = await viewerFixture(page);
  try {
    await page.goto(`/sessions/framing-fixture?render=${render}`);
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    await expect.poll(() => page.evaluate(key => window.__spatialDebug?.inspectVisuals(key)?.meshCount, outlierKey)).toBeGreaterThan(0);
    if (render === 'instanced') expect(await page.evaluate(key => window.__spatialRenderDebug?.projectEntity(key)?.instanceId, outlierKey)).toBeDefined();
    await page.getByRole('button', { name: 'Fit scene', exact: true }).click();
    const canvas = page.locator('canvas');
    await canvas.hover();
    const initial = (await camera(page))!;
    await page.mouse.wheel(0, -400);
    await expect.poll(async () => distance((await camera(page))!.position, initial.position)).toBeGreaterThan(.1);
    const user = (await camera(page))!;
    fixture.remotePose();
    await expect.poll(() => page.evaluate(key => window.__spatialDebug?.getRenderedPose(key)?.p[0], outlierKey)).toBe(1e9);
    await page.setViewportSize({ width: 1100, height: 800 });
    await assertViewRetained(page, user);
    await page.getByRole('button', { name: 'Isometric camera' }).click();
    await page.getByRole('button', { name: 'Fit scene', exact: true }).click();
    await expect.poll(async () => {
      const fit = await camera(page);
      return fit && fit.target.every(value => Math.abs(value) < 10) && distance(fit.position, fit.target) <= 100;
    }).toBe(true);
    await canvas.hover(); await page.mouse.wheel(0, 100000);
    await expect.poll(async () => (await camera(page))?.zoom ?? 0).toBeGreaterThanOrEqual(.1);
    await page.getByRole('button', { name: 'Fit scene', exact: true }).click();
    await expect.poll(async () => (await camera(page))?.zoom).toBe(1);
    expect(await page.evaluate(key => window.__spatialDebug?.getRenderedPose(key)?.p[0], outlierKey)).toBe(1e9);
    await page.screenshot({ path: info.outputPath(`framing-${render}-recovered.png`) });
  } finally { fixture.release(); }
});

test('user navigation during visual loading survives late readiness and explicit fit remains available', async ({ page }) => {
  const fixture = await viewerFixture(page, true);
  try {
    await page.goto('/sessions/framing-fixture');
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    await page.locator('canvas').hover();
    const initial = (await camera(page))!;
    await page.mouse.wheel(0, -400);
    await expect.poll(async () => distance((await camera(page))!.position, initial.position)).toBeGreaterThan(.1);
    const user = (await camera(page))!;
    fixture.release();
    await expect.poll(() => page.evaluate(() => window.__spatialDebug?.inspectVisuals('/World/Lab/FallbackBox')?.source)).toBe('colliders');
    await assertViewRetained(page, user);
    await page.getByRole('button', { name: 'Fit scene', exact: true }).click();
    await expect.poll(async () => distance((await camera(page))!.position, user.position)).toBeGreaterThan(.1);
  } finally { fixture.release(); }
});
