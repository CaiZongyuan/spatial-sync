import { expect, test } from '@playwright/test';
import type { SceneManifest, Snapshot } from '../../packages/protocol/types';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
test.skip(
  process.env.SPATIAL_NEWTON_E2E !== '1',
  'Optional CPU Newton integration; SyntheticSource remains the CI gate',
);

test('Newton survives observer loss, Runner crash, restart, scene reload and page refresh', async ({
  page,
  browser,
  request,
}, testInfo) => {
  test.setTimeout(150_000);
  const initialHealth = await (
    await request.get(`${api}/api/v1/health`)
  ).json();
  expect(
    initialHealth.sessions.filter(
      (session: { state: string }) =>
        !['STOPPED', 'FAILED'].includes(session.state),
    ),
    'Crash probe requires an isolated API with no existing active Session',
  ).toEqual([]);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  let sessionId: string | undefined;
  const observerContext = await browser.newContext({
    viewport: { width: 1440, height: 960 },
  });
  try {
    await page.goto('/scenes/falling-boxes/1?source=newton');
    await page.getByLabel('启动物理源', { exact: true }).selectOption('newton');
    const creating = page.waitForResponse((response) => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST');
    await page.getByRole('button', { name: '启动会话' }).click();
    const created = await creating;
    expect(created.ok(), await created.text()).toBe(true);
    await expect(page.getByTestId('connection-status')).toHaveText(
      '已连接',
      { timeout: 70_000 },
    );
    await expect
      .poll(() =>
        page.evaluate(() => window.__spatialDebug?.getSample()?.seq ?? -1),
      )
      .toBeGreaterThan(0);
    sessionId = new URL(page.url()).pathname.split('/').at(-1)!;
    const statsUrl = `${api}/api/v1/sessions/${sessionId}/stats`;
    const firstStats = await (await request.get(statsUrl)).json();
    const firstPID = firstStats.runnerPID as number;
    expect(Number.isSafeInteger(firstPID) && firstPID > 0).toBe(true);
    expect(firstStats.runnerAlive).toBe(true);
    const observer = await observerContext.newPage();
    await observer.goto(page.url());
    await expect(observer.getByTestId('connection-status')).toHaveText(
      '已连接',
    );
    expect((await (await request.get(statsUrl)).json()).runnerPID).toBe(
      firstPID,
    );
    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('已暂停');
    await expect(observer.getByTestId('session-state')).toHaveText('已暂停');
    const manifest = (await (
      await request.get(`${api}/api/v1/sessions/${sessionId}/scene`)
    ).json()) as SceneManifest;
    const snapshot = (await (
      await request.get(`${api}/api/v1/sessions/${sessionId}/snapshot`)
    ).json()) as Snapshot;
    const site = manifest.sites.find(
      (site) => site.parentBodyKey && Math.hypot(...site.localPose.p) > 0.01,
    )!;
    expect(
      site,
      'Newton fixture includes a genuine nonzero Site offset',
    ).toBeTruthy();
    const body = manifest.nodes.find(
      (node) => node.entityKey === site.parentBodyKey,
    )!;
    if (body.kind !== 'body')
      throw new Error('Site parent must be a streamed Body');
    const expectedBody = snapshot.poses.find(
      (pose) => pose[0] === body.streamId,
    )!;
    await page.getByTestId(`entity-${body.entityKey}`).click();
    await expect(page.getByTestId('inspector-seq')).toHaveText(
      String(snapshot.seq),
    );
    await expect(page.getByTestId('inspector-sim-time')).toHaveText(
      `${snapshot.simTime.toFixed(6)} s`,
    );
    for (let i = 0; i < 3; i++)
      expect(
        Number(
          await page.getByTestId(`world-${['x', 'y', 'z'][i]}`).textContent(),
        ),
      ).toBeCloseTo(expectedBody[i + 1], 5);
    for (let i = 0; i < 4; i++)
      expect(
        Number(await page.getByTestId(`quaternion-${i}`).textContent()),
      ).toBeCloseTo(expectedBody[i + 4], 5);
    const expectedSite = await (
      await request.get(
        `${api}/api/v1/sessions/${sessionId}/pose?key=${encodeURIComponent(site.entityKey)}`,
      )
    ).json();
    expect(expectedSite.seq).toBe(snapshot.seq);
    await page.getByTestId(`entity-${site.entityKey}`).click();
    for (let i = 0; i < 3; i++)
      expect(
        Number(
          await page.getByTestId(`world-${['x', 'y', 'z'][i]}`).textContent(),
        ),
      ).toBeCloseTo(expectedSite.p[i], 5);
    expect(
      Math.hypot(
        ...expectedSite.p.map(
          (value: number, i: number) => value - expectedBody[i + 1],
        ),
      ),
    ).toBeGreaterThan(0.01);
    await page.screenshot({
      path: testInfo.outputPath('newton-site-inspector.png'),
    });

    // This PID came from the Session created above on an isolated API. Never kill a shared Runner.
    process.kill(firstPID, 'SIGKILL');
    await expect(page.getByTestId('session-state')).toHaveText('失败');
    await expect(observer.getByTestId('session-state')).toHaveText('失败');
    expect((await request.get(`${api}/api/v1/health`)).ok()).toBe(true);
    expect((await (await request.get(statsUrl)).json()).runnerAlive).toBe(
      false,
    );
    await page.screenshot({
      path: testInfo.outputPath('newton-runner-failed.png'),
    });
    await page
      .getByRole('button', { name: 'Restart source', exact: true })
      .click();
    await expect(page.getByTestId('session-state')).toHaveText('运行中', {
      timeout: 70_000,
    });
    await expect(page.locator('.revision-label')).not.toHaveText(
      manifest.sceneRevision,
    );
    await expect(observer.getByTestId('session-state')).toHaveText('运行中');
    await expect
      .poll(() =>
        page.evaluate(() => window.__spatialDebug?.getSample()?.seq ?? -1),
      )
      .toBeGreaterThan(snapshot.seq);
    const restarted = await (await request.get(statsUrl)).json();
    expect(restarted.sessionId).toBe(sessionId);
    expect(restarted.runnerPID).not.toBe(firstPID);
    expect(restarted.runnerAlive).toBe(true);
    expect(restarted.sceneRevision).not.toBe(manifest.sceneRevision);
    expect(restarted.seq).toBeGreaterThan(snapshot.seq);
    await page.getByTestId(`entity-${body.entityKey}`).click();
    await expect(page.getByTestId('inspector-seq')).not.toHaveText(
      String(snapshot.seq),
    );

    await page
      .getByRole('button', { name: 'Reload scene', exact: true })
      .click();
    await expect(page.locator('.revision-label')).not.toHaveText(
      restarted.sceneRevision,
    );
    await expect(page.getByTestId('session-state')).toHaveText('运行中');
    const reloaded = await (await request.get(statsUrl)).json();
    expect(reloaded.runnerPID).not.toBe(restarted.runnerPID);
    expect(reloaded.sceneRevision).not.toBe(restarted.sceneRevision);
    await expect
      .poll(() =>
        page.evaluate(() => window.__spatialDebug?.getSample()?.seq ?? -1),
      )
      .toBeGreaterThan(restarted.seq);
    await expect(
      page.getByText('Explore a world coordinate', { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    await expect
      .poll(() =>
        page.evaluate(() => window.__spatialDebug?.getSample()?.seq ?? -1),
      )
      .toBeGreaterThanOrEqual(reloaded.seq);
    expect((await (await request.get(statsUrl)).json()).runnerPID).toBe(
      reloaded.runnerPID,
    );
    await expect(page.locator('canvas')).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath('newton-reloaded.png') });
    await testInfo.attach('newton-runner-identities', {
      body: JSON.stringify(
        {
          firstPID,
          restartedPID: restarted.runnerPID,
          reloadedPID: reloaded.runnerPID,
          sessionId,
          initialRevision: manifest.sceneRevision,
          restartedRevision: restarted.sceneRevision,
          reloadedRevision: reloaded.sceneRevision,
        },
        null,
        2,
      ),
      contentType: 'application/json',
    });
    expect(errors).toEqual([]);
  } finally {
    await observerContext.close();
    if (sessionId) await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});
