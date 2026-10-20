import { expect, test } from '@playwright/test';
import { pathToFileURL } from 'node:url';
import type { SceneManifest } from '../../packages/protocol/types';
import type {} from '../../apps/web/src/components/Workbench';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const native = process.env.SPATIAL_UI_POLISH_SOURCE === 'newton';

test('workbench preserves public controls, tree navigation, exact Inspector and a transparent shadow stage', async ({ page, request }, testInfo) => {
  if (native) test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneVersion: native ? 'robot-and-tubes@2' : 'lab-demo@2', source: native ? 'newton' : 'synthetic', continueWithoutObservers: true },
  });
  expect(created.ok(), await created.text()).toBe(true);
  const { sessionId } = await created.json();
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  try {
    const manifestResponse = await request.get(`${api}/api/v1/sessions/${sessionId}/scene`);
    expect(manifestResponse.ok()).toBe(true);
    const manifest = await manifestResponse.json() as SceneManifest;
    const site = manifest.sites.find(site => site.entityKey.endsWith('/tcp') && site.parentBodyKey)!;
    expect(site).toBeTruthy();
    const body = manifest.nodes.find(node => node.entityKey === site.parentBodyKey)!;
    const tube = manifest.nodes.find(node => node.kind === 'body' && /^Tube\s*0?1$/i.test(node.name))!;
    expect(tube).toBeTruthy();
    await page.goto(`/sessions/${sessionId}?camera=isometric`);
    await expect(page.getByTestId('connection-status')).toHaveText('connected');
    await expect(page.getByTestId('phase-/World/Lab/RobotA')).toBeVisible();
    await expect(page.getByRole('button', { name: `Hide ${tube.name}`, exact: true })).toBeVisible();
    const owner = page.getByTestId(`entity-${body.entityKey}`), marker = page.getByTestId(`entity-${site.entityKey}`);
    await expect(marker).toHaveCount(0);
    await owner.click(); await expect(owner).toHaveCSS('font-size', '13px');
    await expect(owner.locator('..')).toHaveCSS('height', '28px');
    await owner.press('ArrowRight'); await expect(marker).toBeVisible();
    await marker.click(); await marker.press('ArrowLeft');
    await expect(owner).toBeFocused(); await owner.press('ArrowLeft');
    await expect(marker).toHaveCount(0);
    await page.getByLabel('Filter objects', { exact: true }).fill(site.entityKey);
    await marker.click(); await page.getByLabel('Filter objects', { exact: true }).clear();
    await expect(marker).toBeVisible();
    await page.getByLabel('Filter objects', { exact: true }).fill(tube.name);
    await expect(page.getByTestId(`entity-${tube.entityKey}`).locator('mark')).toHaveText(tube.name);
    await expect(page.getByTestId(`entity-${tube.entityKey}`).locator('.tree-parent')).not.toBeEmpty();
    await page.getByLabel('Filter objects', { exact: true }).clear();
    await page.getByRole('checkbox', { name: 'Grid', exact: true }).uncheck();
    await page.getByRole('checkbox', { name: 'Grid', exact: true }).check();
    await page.getByRole('button', { name: 'Realtime factor 2', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Realtime factor 2', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: 'Realtime factor 1', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Realtime factor 1', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: 'Pause simulation', exact: true }).click();
    await expect(page.getByTestId('session-state')).toHaveText('PAUSED');
    await marker.click();
    const poseResponse = await request.get(`${api}/api/v1/sessions/${sessionId}/pose`, { params: { key: site.entityKey } });
    expect(poseResponse.ok()).toBe(true);
    const pose = await poseResponse.json() as { p: number[]; q: number[]; seq: number };
    await expect(page.getByTestId('inspector-seq')).toHaveText(String(pose.seq));
    for (const [index, axis] of ['x', 'y', 'z'].entries()) expect(Number(await page.getByTestId(`world-${axis}`).textContent())).toBeCloseTo(pose.p[index], 5);
    for (const [index, value] of pose.q.entries()) expect(Number(await page.getByTestId(`quaternion-${index}`).textContent())).toBeCloseTo(value, 5);
    const floors = manifest.nodes.filter(node => node.kind !== 'group' && [...node.visuals, ...(node.fallbackVisuals ?? []), ...node.colliders]
      .some(visual => visual.kind === 'primitive' && visual.geometry.type === 'plane')).map(node => node.entityKey);
    expect(floors.length).toBeGreaterThan(0);
    await expect.poll(() => page.evaluate(keys => keys.every(key => window.__spatialDebug?.inspectVisuals(key)?.materials?.some(material =>
      material.type === 'ShadowMaterial' && material.receiveShadow && !material.castShadow)), floors)).toBe(true);
    for (const theme of ['light', 'dark']) {
      await page.getByLabel('Color theme', { exact: true }).selectOption(theme);
      await expect.poll(() => page.evaluate(() => {
        const body = getComputedStyle(document.documentElement).getPropertyValue('--m-body').trim().replace('#', '');
        return window.__spatialRenderDebug?.appearance().materialColors.includes(body);
      })).toBe(true);
      expect(await page.evaluate(() => window.__spatialRenderDebug?.appearance())).toMatchObject({ background: '', alpha: true, clearAlpha: 0 });
      await page.screenshot({ path: testInfo.outputPath(`workbench-${theme}-1440.png`) });
    }
    await testInfo.attach('actual-stage-and-pose.json', { body: JSON.stringify({ sessionId, source: manifest.source, pose,
      rendered: await page.evaluate(keys => ({ appearance: window.__spatialRenderDebug?.appearance(), camera: window.__spatialRenderDebug?.camera(),
        floors: keys.map(key => ({ key, visual: window.__spatialDebug?.inspectVisuals(key) })) }), floors) }), contentType: 'application/json' });
    // A task-local read-only probe can share the same manually selected Newton window.
    const diagnostics = native && process.env.SPATIAL_UI_DIAGNOSTICS
      ? await import(pathToFileURL(process.env.SPATIAL_UI_DIAGNOSTICS).href) : undefined;
    const probeContext = { page, request, api, sessionId, manifest, testInfo };
    if (diagnostics) await diagnostics.observeWorkbench(probeContext);
    await page.getByRole('button', { name: 'Resume simulation', exact: true }).click();
    await expect(page.getByTestId('session-state')).toHaveText('RUNNING');
    if (diagnostics) { await page.goto('/'); await diagnostics.observeOverview(probeContext); }
    expect(errors).toEqual([]);
  } finally {
    const stopped = await request.delete(`${api}/api/v1/sessions/${sessionId}`);
    expect(stopped.ok(), await stopped.text()).toBe(true);
    const after = await (await request.get(`${api}/api/v1/sessions/${sessionId}`)).json();
    expect(after.state).toBe('STOPPED');
    await testInfo.attach('session-cleanup.json', { body: JSON.stringify({ sessionId, deleteStatus: stopped.status(), after }), contentType: 'application/json' });
  }
});
