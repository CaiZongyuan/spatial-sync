import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import type {} from '../../apps/web/src/components/Workbench';
import type { CameraState } from '../../apps/web/src/components/viewer/SceneDiagnostics';
import type { AvailablePreview, PlanDocument } from '../../apps/web/src/lib/plans/model';
import type {} from '../../apps/web/src/components/PlansPage';

const fixture = resolve('../../tests/fixtures/recordings/v1');
const outlierKey = '/World/TestFramingBody';
const camera = (page: Page) => page.evaluate(() => window.__spatialRenderDebug?.camera());
const distance = (a: number[], b: number[]) => Math.hypot(...a.map((value, index) => value - b[index]));

async function readyCamera(page: Page) {
  let pose: CameraState | undefined;
  await expect.poll(async () => { pose = await camera(page); return pose; }).toBeDefined();
  return pose!;
}

/** Real renderer + recorded binary sample, without creating a Session or Source. */
async function viewerFixture(page: Page, delayVisual = false, previewOrder = false) {
  const manifest = JSON.parse(readFileSync(join(fixture, 'manifest.json'), 'utf8'));
  const catalog = JSON.parse(readFileSync(join(fixture, 'catalog.json'), 'utf8'));
  const primitive = manifest.nodes.find((node: { kind: string; visuals: { kind: string; geometry: { type: string } }[] }) =>
    node.kind === 'body' && node.visuals.length && node.visuals.every(visual => visual.kind === 'primitive' && visual.geometry.type !== 'plane'));
  const id = Math.max(...manifest.nodes.filter((node: { kind: string }) => node.kind === 'body')
    .map((node: { streamId: number }) => node.streamId)) + 1;
  manifest.nodes.push({ ...primitive, entityKey: outlierKey, name: 'Framing fixture body', streamId: id });
  if (previewOrder) {
    manifest.nodes = manifest.nodes.filter((node: { kind: string; entityKey: string }) => node.kind === 'group' || node.entityKey === outlierKey);
    manifest.sites = []; manifest.joints = [];
  }
  const segment = readFileSync(join(fixture, 'seg-000001.bin'));
  const original = segment.subarray(19, 10 + segment.readUInt32LE(6));
  const spatial = Buffer.alloc(previewOrder ? 76 : original.length + 32);
  original.copy(spatial);
  const count = previewOrder ? 0 : original.readUInt32LE(40);
  spatial.writeUInt32LE(count + 1, 40);
  if (previewOrder) spatial.writeUInt32LE(1, 12);
  const offset = 44 + count * 32;
  spatial.writeUInt32LE(id, offset);
  [1, 0, 1, 0, 0, 0, 1].forEach((value, index) => spatial.writeFloatLE(value, offset + 4 + index * 4));
  let socket: WebSocketRoute | undefined;
  let sequence = Number(spatial.readBigUInt64LE(16));
  let release!: () => void;
  const visualState = { requested: false, released: false, responseSent: false };
  const held = new Promise<void>(resolve => { release = () => { visualState.released = true; resolve(); }; });
  await page.route('**/api/v1/sessions/framing-fixture/scene', route => route.fulfill({ json: manifest }));
  await page.route('**/api/v1/sessions/framing-fixture/catalog', route => route.fulfill({ json: catalog }));
  await page.route('**/api/v1/sessions/framing-fixture/stats', route => route.fulfill({ json: {} }));
  if (delayVisual) {
    const url = manifest.nodes.find((node: { entityKey: string }) => node.entityKey === '/World/Lab/FallbackBox').visuals[0].url;
    await page.route(`**/${url}`, async route => {
      visualState.requested = true;
      await held; await route.fulfill({ status: 404 });
      visualState.responseSent = true;
    });
  }
  await page.routeWebSocket('**/api/v1/sessions/framing-fixture/ws', route => {
    socket = route;
    route.onMessage(message => {
      const request = JSON.parse(String(message));
      if (request.type === 'client.subscribe') {
        route.send(JSON.stringify({ type: 'subscription.ack', requestId: request.requestId, grantedRateHz: 30 }));
        if (!previewOrder) route.send(spatial);
      }
    });
    route.send(JSON.stringify({ type: 'server.ready', sessionId: 'framing-fixture', role: 'viewer',
      sceneRevision: manifest.sceneRevision, sessionState: 'PAUSED', epoch: 0, seq: sequence,
      simTime: 0, realtimeFactor: 1, heartbeatIntervalMs: 30000 }));
  });
  return { release, visualState, sceneRevision: manifest.sceneRevision as string,
    firstPose() { socket!.send(spatial); }, remotePose() {
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
    const initial = await readyCamera(page);
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

for (const userOwns of [false, true]) test(`initial plan waits for pose/context/preview settlement; user takeover ${userOwns}`, async ({ page }) => {
  const fixture = await viewerFixture(page, false, true);
  const plan: PlanDocument = {
    id: 'plan-framing-order', kind: 'operation', title: 'Disclosed framing-order fixture', target: 'session',
    revision: 1, status: 'approved', steps: [{ id: 1, status: 'pending',
      do: { entity: outlierKey, action: 'moveJoints', params: {} } }],
    sessionId: 'framing-fixture', sceneRevision: fixture.sceneRevision, epoch: 1,
    requestedBy: { kind: 'agent', name: 'framing-fixture' }, createdAtMs: 1,
    approvalId: null, approvalStatus: null, approvalMode: 'auto', stepResults: {}, changes: [],
    persistence: { status: 'stored' },
  };
  const preview: AvailablePreview = {
    planId: plan.id, revision: 1, stepId: 1, sessionId: plan.sessionId, sceneRevision: plan.sceneRevision,
    epoch: 1, available: true, entityKey: outlierKey, simTime: 0, sampleTimeMs: 0,
    points: [[1, 0, 1], [8, 0, 1]], startJointPositions: [], targetJointPositions: [],
    targetPose: { p: [8, 0, 1], q: [0, 0, 0, 1] },
    targetBodyPoses: { [outlierKey]: { p: [8, 0, 1], q: [0, 0, 0, 1] } }, policyDriven: false,
  };
  let releasePreview!: () => void;
  const heldPreview = new Promise<void>(resolve => { releasePreview = resolve; });
  await page.route('**/api/v1/plans?*', route => route.fulfill({ json: { plans: [plan] } }));
  await page.route(`**/api/v1/plans/${plan.id}`, route => route.fulfill({ json: plan }));
  await page.route(`**/api/v1/plans/${plan.id}/steps/1/preview?*`, async route => {
    await heldPreview; await route.fulfill({ json: preview });
  });
  try {
    await page.goto(`/plans/${plan.id}?step=1`);
    const viewport = page.getByTestId('scene-viewport');
    await expect(viewport).toBeVisible();
    await expect(viewport.locator('canvas')).toHaveCount(0);
    const requested = page.waitForRequest(request => request.url().includes(`/${plan.id}/steps/1/preview`));
    fixture.firstPose(); // Authoritative epoch1 sample arrives before the parent's cached context catches up.
    await requested;
    await expect(viewport.locator('canvas')).toBeVisible();
    await expect(viewport).toHaveAttribute('data-camera-fitted', 'false');
    let user: CameraState | undefined;
    if (userOwns) {
      await viewport.locator('canvas').hover();
      const initial = await readyCamera(page);
      await page.mouse.wheel(0, -400);
      await expect.poll(async () => (await camera(page))?.zoom).not.toBe(initial.zoom);
      user = (await camera(page))!;
    }
    releasePreview();
    await expect.poll(() => page.evaluate(key => window.__planViewDebug?.()?.ghosts[key]?.meshes, outlierKey)).toBeGreaterThan(0);
    if (user) {
      await assertViewRetained(page, user);
      await expect(viewport).toHaveAttribute('data-camera-fitted', 'false');
      await page.getByRole('button', { name: 'Fit scene', exact: true }).click();
    }
    await expect(viewport).toHaveAttribute('data-camera-fitted', 'true');
    expect((await page.evaluate(() => window.__planViewDebug?.()))?.targetInView,
      'Actual target projects inside the rendered viewport').toBe(true);
  } finally { releasePreview(); fixture.release(); }
});

for (const unavailableFirstRead of [false, true]) test(`user navigation during visual loading survives late readiness and explicit fit remains available${unavailableFirstRead ? '; disclosed unavailable first camera read' : ''}`, async ({ page }, info) => {
  const fixture = await viewerFixture(page, true);
  try {
    await page.goto('/sessions/framing-fixture');
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    await page.locator('canvas').hover();
    if (unavailableFirstRead) {
      await page.waitForFunction(() => window.__spatialRenderDebug !== undefined, undefined, { timeout: 10_000 });
      await page.evaluate(() => {
        // Only diagnostic publication is delayed; every available pose comes from the real renderer.
        const diagnostics = window.__spatialRenderDebug!;
        const actual = diagnostics.camera;
        const witness = { unavailableReads: 0, readyReads: 0 };
        Object.defineProperty(window, '__cameraReadinessFixture', { value: witness });
        Object.defineProperty(window, '__restoreCameraReadinessFixture', {
          value: () => Object.defineProperty(diagnostics, 'camera', { value: actual }),
        });
        Object.defineProperty(diagnostics, 'camera', { value: () => {
          if (!witness.unavailableReads) { witness.unavailableReads++; return undefined; }
          witness.readyReads++;
          return actual();
        } });
      });
    }
    const initial = await readyCamera(page);
    await expect.poll(() => fixture.visualState.requested).toBe(true);
    expect(fixture.visualState).toEqual({ requested: true, released: false, responseSent: false });
    await page.mouse.wheel(0, -400);
    await expect.poll(async () => distance((await camera(page))!.position, initial.position)).toBeGreaterThan(.1);
    const user = (await camera(page))!;
    expect(fixture.visualState).toEqual({ requested: true, released: false, responseSent: false });
    await info.attach('visual-held-after-navigation.json', { body: JSON.stringify(fixture.visualState), contentType: 'application/json' });
    fixture.release();
    await expect.poll(() => page.evaluate(() => window.__spatialDebug?.inspectVisuals('/World/Lab/FallbackBox')?.source)).toBe('colliders');
    await assertViewRetained(page, user);
    await page.getByRole('button', { name: 'Fit scene', exact: true }).click();
    await expect.poll(async () => distance((await camera(page))!.position, user.position)).toBeGreaterThan(.1);
    if (unavailableFirstRead) {
      const witness = await page.evaluate(() => Object.getOwnPropertyDescriptor(window, '__cameraReadinessFixture')?.value);
      expect(witness.unavailableReads).toBe(1);
      expect(witness.readyReads).toBeGreaterThan(0);
    }
  } finally {
    const visualState = { ...fixture.visualState };
    fixture.release();
    await info.attach('visual-hold-at-cleanup.json', { body: JSON.stringify(visualState), contentType: 'application/json' });
    if (unavailableFirstRead) {
      const witness = await page.evaluate(() => {
        const witness = Object.getOwnPropertyDescriptor(window, '__cameraReadinessFixture')?.value;
        Object.getOwnPropertyDescriptor(window, '__restoreCameraReadinessFixture')?.value();
        return witness;
      });
      await info.attach('camera-readiness-fixture.json', { body: JSON.stringify(witness), contentType: 'application/json' });
    }
  }
});
