import { expect, test } from '@playwright/test';
import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import type {} from '../../apps/web/src/components/Workbench';
import type { ReplaySource } from '../../apps/web/src/lib/replay/replay-source';

const fixture = resolve('../../tests/fixtures/recordings/v1');

for (const version of [1, 2]) test(`plays the v${version} Synthetic fixture with read-only Inspector and independent state comparisons`, async ({ page, baseURL }, testInfo) => {
  const fixture = resolve(`../../tests/fixtures/recordings/v${version}`);
  const expected = JSON.parse(readFileSync(join(fixture, 'expected.json'), 'utf8')) as {
    tubeKey: string; robotKey: string;
    samples: { timeMs: number; seq: number; tubePose: { p: number[]; q: number[] };
      tubeLocation: string; robotJoints: number[] }[];
  };
  const liveRequests: string[] = [];
  page.on('request', (request) => { if (new URL(request.url()).pathname.startsWith('/api/')) liveRequests.push(request.url()); });
  page.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname.startsWith('/api/')) liveRequests.push(socket.url());
  });
  await page.route(`**/fixture/v${version}/**`, async (route) => {
    const relative = new URL(route.request().url()).pathname.split(`/fixture/v${version}/`)[1];
    const path = join(fixture, relative);
    if (!existsSync(path)) await route.fulfill({ status: 404 });
    else await route.fulfill({ path, contentType: relative.endsWith('.json') ? 'application/json' : 'application/octet-stream' });
  });
  const src = new URL(`/fixture/v${version}/recording.json`, baseURL).href;
  await page.goto(`/replay/v${version}?src=${encodeURIComponent(src)}`);
  await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
  await expect(page.getByText('Read-only replay · complete')).toBeVisible();
  await expect(page.getByText(`lab-demo@${version}`, { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Play replay' }).click();
  await expect(page.getByRole('button', { name: 'Pause replay' })).toBeEnabled();
  await expect.poll(() => page.locator('[aria-label="Replay position"]').inputValue().then(Number)).toBeGreaterThan(100);
  await page.getByRole('button', { name: 'Pause replay' }).click();
  await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
  const pausedPose = await page.evaluate(key => window.__spatialDebug?.getAuthoritativePose(key), expected.tubeKey);
  await expect.poll(() => page.evaluate(key => window.__spatialDebug?.getRenderedPose(key), expected.tubeKey)).toEqual(pausedPose);
  const paused = await page.getByTestId('replay-time').textContent();
  await page.getByLabel('Playback speed').selectOption('4');
  await expect(page.getByTestId('replay-time')).toHaveText(paused!);

  for (const sample of expected.samples) {
    await page.locator('[aria-label="Replay position"]').evaluate((element, value) => {
      const input = element as HTMLInputElement;
      input.value = String(value > 0 ? value - 1 : 1);
    }, sample.timeMs);
    await page.getByLabel('Replay position').press(sample.timeMs > 0 ? 'ArrowRight' : 'ArrowLeft');
    await expect.poll(() => page.evaluate(() => window.__spatialDebug?.getSample()?.seq)).toBe(sample.seq);
    await expect.poll(() => page.evaluate(key => window.__spatialDebug?.getRenderedPose(key), expected.tubeKey)).toEqual(sample.tubePose);
    const actual = await page.evaluate(({ tubeKey, robotKey }) => ({
      pose: window.__spatialDebug?.getAuthoritativePose(tubeKey),
      location: window.__spatialDebug?.getEntityState(tubeKey)?.values.location,
      joints: window.__spatialDebug?.getEntityState(robotKey)?.values.jointPositions,
    }), expected);
    expect(actual).toEqual({ pose: sample.tubePose, location: sample.tubeLocation, joints: sample.robotJoints });
  }
  await page.getByTestId(`entity-${expected.robotKey}`).click();
  await page.getByTestId('action-moveJoints').locator('summary').click();
  await expect(page.getByRole('button', { name: 'Run moveJoints', exact: true })).toBeDisabled();
  await expect(page.getByLabel('moveJoints positions', { exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Close session', exact: true })).toBeDisabled();
  expect(liveRequests).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath(`fixture-v${version}-replay.png`) });
});

test('recordings list visibly attributes legacy, exact-version and Live recordings', async ({ page }) => {
  const legacy = JSON.parse(readFileSync(join(fixture, 'recording.json'), 'utf8'));
  const modern = JSON.parse(readFileSync(resolve('../../tests/fixtures/recordings/v2/recording.json'), 'utf8'));
  const live = { ...modern, sessionId: 'live-main', kind: 'live', sceneId: null,
    sceneVersion: null, sceneContentHash: null };
  await page.route('**/api/v1/recordings', route => route.fulfill({ json: [legacy, modern, live] }));
  await page.goto('/recordings');
  const table = page.getByRole('region', { name: 'Recordings', exact: true });
  await expect(table.getByText('lab-demo@1', { exact: true })).toBeVisible();
  await expect(table.getByText('lab-demo@2', { exact: true })).toBeVisible();
  await expect(table.getByText('Live', { exact: true })).toBeVisible();
  await expect(table.getByRole('link', { name: 'Replay' })).toHaveCount(3);
});

test('paused replay draws on asset, view and camera changes while idle frames stop', async ({ page, baseURL }) => {
  let releaseMissing!: () => void;
  const missingAsset = new Promise<void>(resolve => { releaseMissing = resolve; });
  await page.route('**/fixture/demand/**', async route => {
    const relative = new URL(route.request().url()).pathname.split('/fixture/demand/')[1];
    if (relative.startsWith('assets/missing-')) {
      await missingAsset;
      await route.fulfill({ status: 404 });
    } else if (existsSync(join(fixture, relative))) await route.fulfill({ path: join(fixture, relative) });
    else await route.fulfill({ status: 404 });
  });
  try {
    const src = new URL('/fixture/demand/recording.json', baseURL).href;
    await page.goto(`/replay/demand?src=${encodeURIComponent(src)}&render=instanced`);
    await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
    await expect.poll(() => page.evaluate(key => window.__spatialDebug?.getRenderedPose(key), expected.tubeKey)).toBeTruthy();
    expect(await page.evaluate(() => window.__spatialRenderDebug?.frameStats().mode)).toBe('instanced');

    const idle = await page.evaluate(async () => {
      const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      await frame(); await frame();
      window.__spatialDebug!.startProfile();
      await frame(); await frame(); await frame();
      return window.__spatialDebug!.stopProfile();
    });
    expect(idle.samples, 'A settled paused scene must not continuously redraw').toEqual([]);

    await page.evaluate(() => window.__spatialDebug!.startProfile());
    releaseMissing();
    await expect.poll(() => page.evaluate(() =>
      window.__spatialDebug?.inspectVisuals('/World/Lab/FallbackBox')?.source,
    )).toBe('colliders');
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    expect((await page.evaluate(() => window.__spatialDebug!.stopProfile())).samples.length,
      'A late asset fallback must draw while playback stays paused').toBeGreaterThan(0);

    await page.getByTestId(`entity-${expected.tubeKey}`).click();
    await page.getByLabel('Hide Tube 01', { exact: true }).click();
    await expect.poll(() => page.evaluate(key => window.__spatialRenderDebug!.drawnEntities().includes(key), expected.tubeKey)).toBe(false);
    await page.getByLabel('Show Tube 01', { exact: true }).click();
    await expect.poll(() => page.evaluate(key => window.__spatialRenderDebug!.drawnEntities().includes(key), expected.tubeKey)).toBe(true);

    const sample = expected.samples[5];
    await page.getByLabel('Replay position').evaluate((element, value) => {
      (element as HTMLInputElement).value = String(value - 1);
    }, sample.timeMs);
    await page.getByLabel('Replay position').press('ArrowRight');
    await expect.poll(() => page.evaluate(() => window.__spatialDebug?.getSample()?.seq)).toBe(sample.seq);
    await expect.poll(async () => {
      const pose = await page.evaluate(key => window.__spatialDebug?.getRenderedPose(key), expected.tubeKey);
      return pose ? Math.max(...pose.p.map((value, index) => Math.abs(value - sample.tubePose.p[index]))) : Infinity;
    }).toBeLessThan(1e-5);
    const handKey = '/World/Lab/RobotA/Hand';
    const handPose = await page.evaluate(key => window.__spatialDebug?.getAuthoritativePose(key), handKey);
    await expect.poll(() => page.evaluate(key => window.__spatialDebug?.getRenderedPose(key), handKey)).toEqual(handPose);
    const parentedSite = await page.evaluate(() => {
      const debug = window.__spatialRenderDebug!;
      const hand = debug.projectEntity('/World/Lab/RobotA/Hand');
      const site = debug.projectEntity('/World/Lab/RobotA/tcp');
      return { hand, site, pickable: site && debug.pick(site.x, site.y).some(hit => hit.key === '/World/Lab/RobotA/tcp') };
    });
    expect(parentedSite.hand).toBeTruthy();
    expect(parentedSite.site).toBeTruthy();
    expect(parentedSite.site!.x).toBeCloseTo(parentedSite.hand!.x, 6);
    expect(parentedSite.site!.y).toBeCloseTo(parentedSite.hand!.y, 6);
    expect(parentedSite.pickable).toBe(true);

    // Test projection-only mutation before introducing OrbitControls damping.
    const directCamera = await page.evaluate(async () => {
      const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      await frame(); await frame();
      window.__spatialDebug!.startProfile();
      const camera = window.__spatialRenderDebug!.camera();
      window.__spatialRenderDebug!.setCamera({ ...camera, fov: camera.fov - 1 });
      await frame(); await frame();
      return window.__spatialDebug!.stopProfile();
    });
    expect(directCamera.samples.length, 'A direct projection change must invalidate the paused scene').toBeGreaterThan(0);

    const before = await page.evaluate(() => window.__spatialRenderDebug!.camera());
    await page.evaluate(() => window.__spatialDebug!.startProfile());
    await page.locator('.viewport canvas').hover();
    await page.mouse.wheel(0, 120);
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug!.camera().position)).not.toEqual(before.position);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    expect((await page.evaluate(() => window.__spatialDebug!.stopProfile())).samples.length,
      'OrbitControls must draw the changed camera while paused').toBeGreaterThan(0);
    await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
  } finally {
    releaseMissing();
  }
});

test('native End applies the final reliable record at a fractional millisecond duration', async ({ page, baseURL }) => {
  const metadata = JSON.parse(readFileSync(join(fixture, 'recording.json'), 'utf8'));
  const segment = metadata.segments[0];
  const originalSegment = readFileSync(join(fixture, segment.file));
  let lastSeq = 0;
  let lastSpatial = Buffer.alloc(0);
  for (let offset = 6; offset < originalSegment.length;) {
    if (originalSegment.readUInt8(offset + 4) === 1) {
      lastSeq = Number(originalSegment.readBigUInt64LE(offset + 29));
      lastSpatial = originalSegment.subarray(offset + 13, offset + 4 + originalSegment.readUInt32LE(offset));
    }
    offset += 4 + originalSegment.readUInt32LE(offset);
  }
  // Independent raw NSYN tail oracle, beyond the tenth golden's earlier time.
  let lastHandPose: { p: number[]; q: number[] } | undefined;
  for (let offset = 44; offset < lastSpatial.length; offset += 32) {
    if (lastSpatial.readUInt32LE(offset) !== 3) continue;
    const pose = Array.from({ length: 7 }, (_, index) => lastSpatial.readFloatLE(offset + 4 + index * 4));
    const q = pose.slice(3);
    const norm = Math.hypot(...q);
    lastHandPose = { p: pose.slice(0, 3), q: q.map(value => value / norm) };
  }
  expect(lastHandPose).toBeTruthy();
  const duration = Math.ceil(segment.endSampleTimeMs - segment.startSampleTimeMs) + 0.75;
  const terminal = Buffer.from(JSON.stringify({
    type: 'session.state', sessionId: metadata.sessionId,
    sceneRevision: metadata.sceneRevision, state: 'STOPPED', epoch: 0,
    seq: lastSeq, simTime: segment.endSimTime,
    realtimeFactor: 1, actualRtf: 0, cause: { kind: 'lifecycle' },
  }));
  const header = Buffer.alloc(13);
  header.writeUInt32LE(9 + terminal.length, 0);
  header.writeUInt8(2, 4);
  header.writeDoubleLE(segment.startSampleTimeMs + duration, 5);
  const binary = Buffer.concat([originalSegment, header, terminal]);
  segment.endSampleTimeMs = segment.startSampleTimeMs + duration;
  segment.bytes = binary.length;
  const actualDuration = segment.endSampleTimeMs - segment.startSampleTimeMs;
  metadata.durationMs = actualDuration;
  await page.route('**/fixture/fractional/**', async (route) => {
    const relative = new URL(route.request().url()).pathname.split('/fixture/fractional/')[1];
    if (relative === 'recording.json') await route.fulfill({ json: metadata });
    else if (relative === segment.file) await route.fulfill({ body: binary, contentType: 'application/octet-stream' });
    else if (!existsSync(join(fixture, relative))) await route.fulfill({ status: 404 });
    else await route.fulfill({ path: join(fixture, relative) });
  });
  const src = new URL('/fixture/fractional/recording.json', baseURL).href;
  await page.goto(`/replay/fractional?src=${encodeURIComponent(src)}`);
  await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
  await page.evaluate(async () => {
    const moduleUrl = performance.getEntriesByType('resource').map(entry => entry.name)
      .find(url => url.includes('/src/lib/replay/replay-source.ts'))!;
    const { ReplaySource } = await import(/* @vite-ignore */ moduleUrl);
    const original = ReplaySource.prototype.seek;
    (window as unknown as { __tailReceipt?: unknown }).__tailReceipt = undefined;
    ReplaySource.prototype.seek = async function(this: ReplaySource, position: number) {
      (window as unknown as { __clearReplayCache: () => void }).__clearReplayCache = () => {
        (this as unknown as { segments: Map<number, unknown> }).segments.clear();
      };
      await original.call(this, position);
      (window as unknown as { __tailReceipt?: unknown }).__tailReceipt = {
        requested: position, actual: this.getPlayback().timeMs,
        sessionState: this.getSnapshot().sessionState,
      };
    };
  });
  await page.getByRole('button', { name: 'Play replay' }).click();
  await expect.poll(() => page.getByLabel('Replay position').inputValue().then(Number)).toBeGreaterThan(100);
  await page.getByLabel('Replay position').press('Home');
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { __tailReceipt?: unknown }).__tailReceipt,
  )).toEqual({ requested: 0, actual: 0, sessionState: 'RUNNING' });
  await expect.poll(() => page.getByLabel('Replay position').inputValue().then(Number)).toBeGreaterThan(100);
  const resumed = await page.evaluate(async () => {
    const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    window.__spatialDebug!.startProfile();
    await frame(); await frame(); await frame();
    return window.__spatialDebug!.stopProfile();
  });
  expect(resumed.samples.length, 'Playing must resume continuous frames after a seek').toBeGreaterThanOrEqual(2);
  await page.getByLabel('Replay position').press('End');
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { __tailReceipt?: { actual: number; sessionState: string } }).__tailReceipt,
  )).toEqual({ requested: Math.ceil(actualDuration), actual: actualDuration, sessionState: 'STOPPED' });
  await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
  expect(await page.evaluate(() => window.__spatialDebug?.getSample()?.seq)).toBe(lastSeq);
  await expect.poll(() => page.evaluate(() => window.__spatialDebug?.getRenderedPose('/World/Lab/RobotA/Hand'))).toEqual(lastHandPose);
  await expect.poll(() => page.evaluate(key => window.__spatialDebug?.getRenderedPose(key), expected.tubeKey)).toEqual(expected.samples.at(-1)!.tubePose);
  const atEnd = await page.evaluate(async () => {
    const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    await frame(); await frame();
    window.__spatialDebug!.startProfile();
    await frame(); await frame(); await frame();
    return window.__spatialDebug!.stopProfile();
  });
  expect(atEnd.samples, 'EOF must draw the final pose and then stop idle redraws').toEqual([]);
  await page.evaluate(() => (window as unknown as { __clearReplayCache: () => void }).__clearReplayCache());
  await page.getByLabel('Replay position').press('Home');
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { __tailReceipt?: { actual: number; sessionState: string } }).__tailReceipt,
  )).toEqual({ requested: 0, actual: 0, sessionState: 'RUNNING' });
  expect(await page.evaluate(() => window.__spatialDebug?.getSample()?.seq)).toBe(expected.samples[0].seq);
});
