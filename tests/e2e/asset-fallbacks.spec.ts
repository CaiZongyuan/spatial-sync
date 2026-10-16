import {
  expect,
  test,
  type Page,
  type APIRequestContext,
} from '@playwright/test';
import type { SceneManifest } from '../../packages/protocol/types';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
test.skip(
  process.env.SPATIAL_NEWTON_E2E !== '1',
  'Optional real Newton/USD/robot asset integration',
);

async function launch(page: Page, request: APIRequestContext, sceneId: string) {
  const health = await (await request.get(`${api}/api/v1/health`)).json();
  expect(
    health.sessions.filter(
      (session: { state: string }) =>
        !['STOPPED', 'FAILED'].includes(session.state),
    ),
  ).toEqual([]);
  await page.goto('/');
  await page.getByLabel('Scene', { exact: true }).selectOption(sceneId);
  await page.getByLabel('Source', { exact: true }).selectOption('newton');
  const creating = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/v1/sessions') &&
      response.request().method() === 'POST',
    { timeout: 180_000 },
  );
  await page.getByRole('button', { name: 'Launch workspace' }).click();
  const created = await creating;
  expect(created.ok(), await created.text()).toBe(true);
  await expect(page.getByTestId('connection-status')).toHaveText('connected');
  const sid = new URL(page.url()).pathname.split('/').at(-1)!;
  const manifest = (await (
    await request.get(`${api}/api/v1/sessions/${sid}/scene`)
  ).json()) as SceneManifest;
  return { sid, manifest };
}

test('real Y-up GLB and native mesh fallback preserve world bounds and exclusive groups', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(180_000);
  let sid: string | undefined;
  try {
    const launched = await launch(page, request, 'usd-lab');
    sid = launched.sid;
    const moving = launched.manifest.nodes.find(
      (node) => node.entityKey === '/World/MovingPart',
    )!;
    const housing = launched.manifest.nodes.find(
      (node) => node.entityKey === '/World/Equipment/Housing',
    )!;
    if (moving.kind !== 'body' || housing.kind === 'group')
      throw new Error('USD fixture shapes missing');
    expect(moving.visuals[0].kind).toBe('glb');
    expect(moving.fallbackVisuals?.[0].kind).toBe('mesh');
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            window.__spatialDebug?.inspectVisuals('/World/MovingPart')?.source,
        ),
      )
      .toBe('fallbackVisuals');
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            window.__spatialDebug?.inspectVisuals('/World/Equipment/Housing')
              ?.source,
        ),
      )
      .toBe('visuals');
    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('PAUSED');
    await page.getByRole('button', { name: 'Reset simulation' }).click();
    await expect
      .poll(() =>
        page.evaluate(() => window.__spatialDebug?.getSample()?.simTime),
      )
      .toBe(0);
    const expectedBounds = [
      {
        key: moving.entityKey,
        source: 'fallbackVisuals',
        count: 2,
        min: [0.9, 1.3, -0.1],
        max: [1.1, 1.7, 0.5],
      },
      {
        key: housing.entityKey,
        source: 'visuals',
        count: 1,
        min: [-1.1, 0, -0.2],
        max: [-0.5, 0.6, 0.2],
      },
    ];
    for (const expected of expectedBounds) {
      await expect
        .poll(async () => {
          const actual = await page.evaluate(
            (key) => window.__spatialDebug?.inspectVisuals(key),
            expected.key,
          );
          return actual
            ? Math.max(
                ...actual.min.map((value, i) =>
                  Math.abs(value - expected.min[i]),
                ),
                ...actual.max.map((value, i) =>
                  Math.abs(value - expected.max[i]),
                ),
              )
            : 1;
        })
        .toBeLessThan(1e-4);
      const visual = await page.evaluate(
        (key) => window.__spatialDebug!.inspectVisuals(key),
        expected.key,
      );
      expect(visual).toMatchObject({
        source: expected.source,
        visualCount: expected.count,
        meshCount: expected.count,
        normalsComplete: true,
      });
    }
    await page.getByTestId(`entity-${moving.entityKey}`).click();
    await page.screenshot({
      path: testInfo.outputPath('usd-native-mesh-fallback.png'),
    });
    const mesh = moving.fallbackVisuals![0];
    if (mesh.kind !== 'mesh') throw new Error('Missing native mesh fixture');
    const meshResponse = await request.get(`${api}${mesh.url}`);
    expect(meshResponse.status()).toBe(200);
    expect(meshResponse.headers()['content-type']).toContain(
      'model/gltf-binary',
    );
    expect(
      (
        await request.get(`${api}${mesh.url}`, {
          headers: { 'If-None-Match': meshResponse.headers().etag },
        })
      ).status(),
    ).toBe(304);

    // Drop only the native mesh HTTP request; poses and every remaining asset still use the real gateway.
    await page.route('**/meshes/*', (route) => route.abort('failed'));
    await page.reload();
    await expect(page.getByTestId('connection-status')).toHaveText('connected');
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            window.__spatialDebug?.inspectVisuals('/World/MovingPart')?.source,
        ),
      )
      .toBe('colliders');
    const collider = await page.evaluate(() =>
      window.__spatialDebug!.inspectVisuals('/World/MovingPart'),
    );
    expect(collider).toMatchObject({ visualCount: 1, meshCount: 1 });
    await page.getByLabel('Colliders', { exact: true }).check();
    const unchanged = await page.evaluate(() =>
      window.__spatialDebug!.inspectVisuals('/World/MovingPart'),
    );
    expect(unchanged).toMatchObject({
      source: 'colliders',
      visualCount: 1,
      meshCount: 1,
    });
    await page.getByRole('button', { name: /Diagnostics/ }).click();
    await page
      .getByText('Compatibility & asset warnings')
      .scrollIntoViewIfNeeded();
    await expect(
      page.getByText('Compatibility & asset warnings'),
    ).toBeInViewport();
    await page.screenshot({
      path: testInfo.outputPath('usd-collider-fallback-warnings.png'),
    });
  } finally {
    if (sid) await request.delete(`${api}/api/v1/sessions/${sid}`);
  }
});

test('real Franka robot renders every simultaneous native visual and exposes its TCP pose', async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(240_000);
  let sid: string | undefined;
  try {
    const launched = await launch(page, request, 'robot-and-tubes');
    sid = launched.sid;
    const bodies = launched.manifest.nodes.filter(
      (node) => node.kind === 'body',
    );
    expect(bodies.length).toBeGreaterThan(25);
    await expect
      .poll(
        () =>
          page.evaluate(
            (keys) =>
              keys.every((key) => window.__spatialDebug?.inspectVisuals(key)),
            bodies.map((node) => node.entityKey),
          ),
        { timeout: 30_000 },
      )
      .toBe(true);
    const multipleVisuals = bodies.filter((node) => node.visuals.length > 1);
    expect(multipleVisuals.length).toBeGreaterThan(0);
    for (const node of multipleVisuals) {
      const visual = await page.evaluate(
        (key) => window.__spatialDebug!.inspectVisuals(key),
        node.entityKey,
      );
      expect(visual).toMatchObject({
        source: 'visuals',
        visualCount: node.visuals.length,
        normalsComplete: true,
      });
    }
    const tcp = launched.manifest.sites.find((site) =>
      site.entityKey.endsWith('/tcp'),
    )!;
    expect(tcp).toBeTruthy();
    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('PAUSED');
    const expected = await (
      await request.get(
        `${api}/api/v1/sessions/${sid}/pose?key=${encodeURIComponent(tcp.entityKey)}`,
      )
    ).json();
    await page.getByLabel('Filter objects').fill(tcp.entityKey);
    await page.getByTestId(`entity-${tcp.entityKey}`).click();
    await expect(page.getByTestId('inspector-seq')).toHaveText(
      String(expected.seq),
    );
    for (let i = 0; i < 3; i++)
      expect(
        Number(
          await page.getByTestId(`world-${['x', 'y', 'z'][i]}`).textContent(),
        ),
      ).toBeCloseTo(expected.p[i], 5);
    await page.getByLabel('Filter objects').clear();
    await page.screenshot({
      path: testInfo.outputPath('franka-tcp-inspector.png'),
    });
  } finally {
    if (sid) await request.delete(`${api}/api/v1/sessions/${sid}`);
  }
});
