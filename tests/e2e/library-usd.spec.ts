import { expect, test, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import type {} from '../../apps/web/src/components/Workbench';
import type { SceneManifest } from '../../packages/protocol/types';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const version = 'usd-library-fixture@1';
const tube = '/World/Lab/Tubes/Tube1';
const room = '/World/Dressing/Room';

test.skip(process.env.SPATIAL_NEWTON_E2E !== '1', 'Optional actual CPU USD library journey');

function quantizationLimit(actual: number, expected: number, digits: number) {
  return .5 * 10 ** -digits + 4 * Number.EPSILON * Math.max(1, Math.abs(actual), Math.abs(expected));
}

async function expectPose(page: Page, key: string, expected: { p: number[]; q: number[] }) {
  await expect(async () => {
    const actual = await page.evaluate(key => window.__spatialDebug?.getRenderedPose(key), key);
    expect(actual).toBeDefined();
    for (const [field, count, digits] of [['p', 3, 6], ['q', 4, 7]] as const) {
      expect(actual![field]).toHaveLength(count);
      expect(expected[field]).toHaveLength(count);
      actual![field].forEach((value, index) => {
        const reference = expected[field][index];
        expect(Number.isFinite(value) && Number.isFinite(reference)).toBe(true);
        expect(Math.abs(value - reference)).toBeLessThanOrEqual(quantizationLimit(value, reference, digits));
      });
    }
  }).toPass({ timeout: 10_000 });
}

test('actual USD library PBR and dressing remain portable in offline replay', async ({ page, request }, info) => {
  test.setTimeout(90_000);
  let sid: string | undefined;
  const errors: string[] = [];
  const loaded = new Set<string>();
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.pathname.startsWith('/api/v1/library/files/') && url.pathname.endsWith('.glb') && response.ok())
      loaded.add(url.pathname);
  });
  try {
    const before = await (await request.get(`${api}/api/v1/health`)).json();
    expect(before.sessions.filter((session: { state: string }) => !['STOPPED', 'FAILED'].includes(session.state)))
      .toEqual([]);
    const detail = await (await request.get(`${api}/api/v1/library/scenes/usd-library-fixture/1`)).json();
    expect(detail.id).toBe(version);
    expect(detail.launchable).toBe(true);
    expect(detail.validation.physics).toMatchObject({ status: 'completed', device: 'cpu' });
    await page.goto('/scenes/usd-library-fixture/1?source=newton');
    await page.getByLabel('启动物理源', { exact: true }).selectOption('newton');
    const creating = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/sessions'
      && response.request().method() === 'POST', { timeout: 60_000 });
    await page.getByRole('button', { name: '启动会话', exact: true }).click();
    const response = await creating;
    expect(response.status(), await response.text()).toBe(201);
    sid = (await response.json()).sessionId;
    await expect(page.getByTestId('connection-status')).toHaveText('connected');
    const route = `${api}/api/v1/sessions/${sid}`;
    const manifest = await (await request.get(`${route}/scene`)).json() as SceneManifest;
    const bodies = manifest.nodes.filter(node => node.kind === 'body');
    expect(bodies).toHaveLength(3);
    expect(manifest.sites.filter(site => site.entityKey.startsWith('/World/Lab/Stations/'))).toHaveLength(2);
    const stats = await (await request.get(`${route}/stats`)).json();
    expect(stats).toMatchObject({ device: 'cpu', modelBodyCount: 3, shapeCount: 7,
      readbackBodyCount: 3, readbackBytes: 84, declaredWeldCount: 0, activeWeldCount: 0, runnerAlive: true });
    expect(stats.readerThreadsAlive).toHaveLength(2);
    const provided = new Map<string, string>();
    for (const key of [tube, '/World/Lab/Tubes/Tube2', '/World/Lab/Table', room]) {
      const node = manifest.nodes.find(node => node.entityKey === key)!;
      if (node.kind === 'group') throw new Error(`Missing actual physical/static node: ${key}`);
      expect(node.visuals[0].kind).toBe('glb');
      const visualAsset = node.visuals[0];
      if (visualAsset.kind !== 'glb') throw new Error('Provided PBR visual missing');
      const original = await request.get(`${api}${visualAsset.url}`);
      expect(original.status()).toBe(200);
      provided.set(key, createHash('sha256').update(await original.body()).digest('hex'));
      await expect.poll(() => page.evaluate(key => window.__spatialDebug?.inspectVisuals(key)?.source, key))
        .toBe('visuals');
      const visual = await page.evaluate(key => window.__spatialDebug?.inspectVisuals(key), key);
      expect(visual).toMatchObject({ visualCount: 1, normalsComplete: true });
      expect(visual!.meshCount).toBe(key === room ? 2 : 1);
    }
    expect(loaded.size).toBe(3);
    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('PAUSED');
    await page.getByRole('button', { name: 'Step simulation' }).click();
    await page.getByRole('button', { name: 'Reset simulation' }).click();
    await expect.poll(() => page.evaluate(() => window.__spatialDebug?.getSample()?.simTime)).toBe(0);
    const snapshot = await (await request.get(`${route}/snapshot`)).json();
    const tubeNode = bodies.find(node => node.entityKey === tube)!;
    if (tubeNode.kind !== 'body') throw new Error('Missing authoritative tube body');
    const pose = snapshot.poses.find((row: number[]) => row[0] === tubeNode.streamId) as number[];
    const expectedPose = { p: pose.slice(1, 4), q: pose.slice(4) };
    await expectPose(page, tube, expectedPose);
    const liveReceipt = { manifest, snapshot, stats, loaded: [...loaded] };
    await info.attach('actual-cpu-source.json', { body: JSON.stringify(liveReceipt, null, 2), contentType: 'application/json' });
    await page.screenshot({ path: info.outputPath('usd-library-live.png'), fullPage: true });
    expect((await request.delete(route)).status()).toBe(204);
    const recording = await (await request.get(`${api}/recordings/${sid}/recording.json`)).json();
    expect(recording).toMatchObject({ formatVersion: 2, status: 'complete', sceneVersion: version });
    expect(recording.segments).toHaveLength(1);
    const frozen = await (await request.get(`${api}/recordings/${sid}/manifest.json`)).json() as SceneManifest;
    expect(JSON.stringify(frozen)).not.toContain('/api/v1/');
    const urls = new Set<string>();
    for (const node of frozen.nodes) {
      if (node.kind === 'group') continue;
      const digest = provided.get(node.entityKey);
      if (digest) expect(node.visuals[0]).toMatchObject({ kind: 'glb', url: `assets/sha256-${digest}.glb` });
      for (const visual of [...node.visuals, ...(node.fallbackVisuals ?? []), ...node.colliders])
        if (visual.kind !== 'primitive') urls.add(visual.url);
    }
    expect(urls.size).toBe(5); // Three provided GLBs plus the table and Site native meshes.
    for (const url of urls) {
      const asset = await request.get(`${api}/recordings/${sid}/${url}`);
      expect(asset.status()).toBe(200);
      expect(url).toBe(`assets/sha256-${createHash('sha256').update(await asset.body()).digest('hex')}.glb`);
    }
    const live: string[] = [];
    const developmentSockets: string[] = [];
    page.on('request', request => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith('/api/v1/sessions') || path.startsWith('/api/v1/library/files/')) live.push(request.url());
    });
    page.on('websocket', socket => {
      const url = new URL(socket.url());
      if (url.host === new URL(page.url()).host && url.pathname === '/' && url.searchParams.has('token')) {
        url.searchParams.set('token', '<REDACTED>');
        developmentSockets.push(url.href); // Vite development reload channel, outside the session API.
      } else live.push(socket.url());
    });
    await page.goto(`/replay/${sid}`);
    await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
    await expect(page.getByText('Read-only replay · complete')).toBeVisible();
    await page.getByLabel('Replay position').press('End');
    await expectPose(page, tube, expectedPose);
    await expect.poll(() => page.evaluate(key => window.__spatialDebug?.inspectVisuals(key)?.source, room))
      .toBe('visuals');
    await expect(page.getByRole('button', { name: 'Close session', exact: true })).toBeDisabled();
    expect(live).toEqual([]);
    expect(errors).toEqual([]);
    await info.attach('portable-replay.json', { body: JSON.stringify({ recording, urls: [...urls], live,
      developmentSockets }, null, 2),
      contentType: 'application/json' });
    await page.screenshot({ path: info.outputPath('usd-library-replay.png'), fullPage: true });
  } finally {
    if (sid) await request.delete(`${api}/api/v1/sessions/${sid}`);
  }
});
