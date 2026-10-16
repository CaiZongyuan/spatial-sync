import { expect, test } from '@playwright/test';
import type {} from '../../apps/web/src/components/Workbench';
import type { SceneManifest, Snapshot } from '../../packages/protocol/types';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('full binary frames retain instanced poses, picking and object visibility', async ({ page, request }) => {
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneId: 'lab-demo', source: 'synthetic' },
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  const route = `${api}/api/v1/sessions/${sessionId}`;
  try {
    const manifest = (await (await request.get(`${route}/scene`)).json()) as SceneManifest;
    await page.goto(`/sessions/${sessionId}?render=instanced`);
    await expect(page.getByTestId('connection-status')).toHaveText('connected');
    await expect.poll(() => page.evaluate(() => window.__spatialDebug?.getSample()?.seq ?? -1)).toBeGreaterThan(0);
    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('PAUSED');
    const snapshot = (await (await request.get(`${route}/snapshot`)).json()) as Snapshot;
    await expect.poll(() => page.evaluate(() => window.__spatialRenderDebug?.frameStats().appliedBodies)).toBe(snapshot.poses.length);
    const bodies = manifest.nodes.filter((node) => node.kind === 'body');
    for (const body of bodies) {
      const expected = snapshot.poses.find((pose) => pose[0] === body.streamId)!;
      const pose = await page.evaluate((key) => window.__spatialDebug?.getRenderedPose(key), body.entityKey);
      expect(pose).toBeTruthy();
      pose!.p.forEach((value, i) => expect(value).toBeCloseTo(expected[i + 1], 5));
      const alignment = pose!.q.reduce((sum, value, i) => sum + value * expected[i + 4], 0);
      expect(Math.abs(alignment)).toBeCloseTo(1, 5);
    }
    await page.evaluate(() => window.__spatialRenderDebug!.fitScene());
    const target = await page.evaluate((nodes) => {
      const debug = window.__spatialRenderDebug!;
      for (const node of nodes) {
        const point = debug.projectEntity(node.entityKey);
        if (point?.instanceId !== undefined && debug.pick(point.x, point.y)[0]?.key === node.entityKey)
          return { ...point, key: node.entityKey, name: node.name };
      }
    }, bodies);
    expect(target, 'At least one unobstructed instanced body must be pickable').toBeTruthy();
    await page.mouse.click(target!.x, target!.y);
    await expect(page.locator('.inspector-panel .entity-key')).toHaveText(target!.key);
    await page.getByLabel(`Hide ${target!.name}`, { exact: true }).click();
    await expect.poll(() => page.evaluate((key) => window.__spatialRenderDebug!.drawnEntities().includes(key), target!.key)).toBe(false);
    await page.getByLabel(`Show ${target!.name}`, { exact: true }).click();
    await expect.poll(() => page.evaluate((key) => window.__spatialRenderDebug!.drawnEntities().includes(key), target!.key)).toBe(true);
  } finally {
    await request.delete(route);
  }
});
