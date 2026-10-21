import { test, diagnosticEvaluate } from './observer-diagnostic';
import { expect, type APIRequestContext, type BrowserContext, type Page, type WebSocketRoute } from '@playwright/test';
import type { SceneManifest, Snapshot } from '../../packages/protocol/types';
import { cleanupObserver } from './cleanup-observer';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

async function waitConnected(page: Page) {
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');
  await expect
    .poll(() =>
      diagnosticEvaluate(page, () => {
        const value = window.__spatialDebug?.getSample()?.seq ?? -1;
        (window as Window & { __observerDiagnostic?: (kind: string, value: unknown) => void }).__observerDiagnostic?.('getter', { seq: value });
        return value;
      }),
    )
    .toBeGreaterThanOrEqual(0);
  await expect(page.locator('canvas')).toBeVisible();
}

async function startRotor(request: APIRequestContext, sessionId: string) {
  const closed = await request.post(
    `${api}/api/v1/sessions/${sessionId}/actions`,
    {
      data: {
        requestId: 'workbench-close-lid',
        entityKey: '/World/Lab/Centrifuge',
        action: 'closeLid',
        params: {},
        wait: true,
        timeoutSeconds: 5,
      },
    },
  );
  expect(closed.ok()).toBe(true);
  expect((await closed.json()).status).toBe('succeeded');
  const started = await request.post(
    `${api}/api/v1/sessions/${sessionId}/actions`,
    {
      data: {
        requestId: 'workbench-start',
        entityKey: '/World/Lab/Centrifuge',
        action: 'start',
        params: { rpm: 500, durationSeconds: 60 },
      },
    },
  );
  expect(started.status()).toBe(202);
  expect(['accepted', 'running']).toContain((await started.json()).status);
}

async function resetAndResume(page: Page, observer: Page, snapshot: Snapshot) {
  await page.getByRole('button', { name: 'Reset simulation' }).click();
  await expect(page.getByTestId('epoch')).toHaveText(
    String(snapshot.epoch + 1),
  );
  await expect(observer.getByTestId('epoch')).toHaveText(
    String(snapshot.epoch + 1),
  );
  await expect
    .poll(() =>
      page.evaluate(() => window.__spatialDebug?.getSample()?.simTime),
    )
    .toBe(0);
  await expect(page.getByTestId('session-state')).toHaveText('已暂停');
  await page.getByRole('button', { name: 'Resume simulation' }).click();
  await expect(observer.getByTestId('session-state')).toHaveText('运行中');
  await expect
    .poll(() =>
      page.evaluate(() => window.__spatialDebug?.getSample()?.simTime ?? 0),
    )
    .toBeGreaterThan(0);
}

test('real SyntheticSource workbench launches and compares exact Body and Site poses', async ({ page, request }, testInfo) => {
  let sessionId: string | undefined;
  const failures: unknown[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto('/scenes/lab-demo/1?source=synthetic');
    await page.getByLabel('启动物理源', { exact: true }).selectOption('synthetic');
    const creating = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/sessions'
      && response.request().method() === 'POST').then(async response => {
        expect(response.ok()).toBe(true);
        sessionId = (await response.json() as { sessionId: string }).sessionId;
      });
    const setup = await Promise.allSettled([
      page.getByRole('button', { name: '启动会话' }).click(),
      creating,
    ]);
    for (const result of setup) if (result.status === 'rejected') failures.push(result.reason);
    if (failures.length) return;
    await waitConnected(page);
    if ((await page.getByTestId('session-state').getAttribute('data-state')) === 'PAUSED') {
      await page.getByRole('button', { name: 'Resume simulation' }).click();
      await expect(page.getByTestId('session-state')).toHaveText('运行中');
    }
    expect(new URL(page.url()).pathname.split('/').at(-1)).toBe(sessionId);
    const manifestResponse = await request.get(
      `${api}/api/v1/sessions/${sessionId}/scene`,
    );
    expect(manifestResponse.ok()).toBe(true);
    const manifest = (await manifestResponse.json()) as SceneManifest;
    const site = manifest.sites.find(
      (site) =>
        site.parentBodyKey === '/World/Lab/Centrifuge/Rotor' &&
        Math.hypot(...site.localPose.p) > 0.01,
    )!;
    expect(site).toBeTruthy();
    const body = manifest.nodes.find(
      (node) => node.entityKey === site.parentBodyKey && node.kind === 'body',
    )!;
    if (body.kind !== 'body')
      throw new Error('Synthetic fixture requires a streamed body');
    await expect
      .poll(() =>
        page.evaluate(
          (key) => window.__spatialDebug?.getRenderedPose(key)?.p.length ?? 0,
          body.entityKey,
        ),
      )
      .toBe(3);
    const initialRendered = await page.evaluate(
      (key) => window.__spatialDebug?.getRenderedPose(key),
      body.entityKey,
    );
    await startRotor(request, sessionId!);
    await expect
      .poll(async () =>
        JSON.stringify(
          await page.evaluate(
            (key) => window.__spatialDebug?.getRenderedPose(key),
            body.entityKey,
          ),
        ),
      )
      .not.toBe(JSON.stringify(initialRendered));

    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('已暂停');
    await expect(
      page.getByText('命令已应用', { exact: true }),
    ).toBeVisible();
    const snapshot = (await (
      await request.get(`${api}/api/v1/sessions/${sessionId}/snapshot`)
    ).json()) as Snapshot;
    const expected = snapshot.poses.find((pose) => pose[0] === body.streamId)!;
    await page.getByTestId(`entity-${body.entityKey}`).click();
    await expect(page.getByTestId('inspector-seq')).toHaveText(
      String(snapshot.seq),
    );
    await expect(page.getByTestId('inspector-sim-time')).toHaveText(
      `${snapshot.simTime.toFixed(6)} s`,
    );
    for (let i = 0; i < 3; i++)
      expect(Number(await page.getByTestId(`world-${['x', 'y', 'z'][i]}`).textContent())).toBeCloseTo(expected[i + 1], 5);
    for (let i = 0; i < 4; i++)
      expect(
        Number(await page.getByTestId(`quaternion-${i}`).textContent()),
      ).toBeCloseTo(expected[i + 4], 5);
    await expect
      .poll(async () => {
        const rendered = await page.evaluate(
          (key) => window.__spatialDebug?.getRenderedPose(key),
          body.entityKey,
        );
        return rendered
          ? Math.max(
              ...rendered.p.map((value, i) => Math.abs(value - expected[i + 1])),
            )
          : 1;
      })
      .toBeLessThan(1e-5);
    // Independent quaternion-vector oracle, without the viewer's Three pose helper.
    const [vx, vy, vz] = site.localPose.p;
    const [, px, py, pz, qx, qy, qz, qw] = expected;
    const tx = 2 * (qy * vz - qz * vy),
      ty = 2 * (qz * vx - qx * vz),
      tz = 2 * (qx * vy - qy * vx);
    const siteExpected = [
      px + vx + qw * tx + qy * tz - qz * ty,
      py + vy + qw * ty + qz * tx - qx * tz,
      pz + vz + qw * tz + qx * ty - qy * tx,
    ];
    await page.getByTestId(`entity-${body.entityKey}`).press('ArrowRight');
    await page.getByTestId(`entity-${site.entityKey}`).click();
    for (let i = 0; i < 3; i++)
      expect(
        Number(
          await page.getByTestId(`world-${['x', 'y', 'z'][i]}`).textContent(),
        ),
      ).toBeCloseTo(siteExpected[i], 5);
    expect(
      Math.hypot(...siteExpected.map((value, i) => value - expected[i + 1])),
    ).toBeGreaterThan(0.01);
    await page.screenshot({
      path: testInfo.outputPath('workbench-site-inspector.png'),
    });

    expect(errors).toEqual([]);
  } catch (error) {
    failures.push(error);
  } finally {
    await cleanupObserver(undefined, sessionId ? [`${api}/api/v1/sessions/${sessionId}`] : [], failures);
  }
});

test('real SyntheticSource observers share pause, heartbeat, Step, Reset and Resume', async ({ page, browser, request }) => {
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneVersion: 'lab-demo@1', source: 'synthetic', continueWithoutObservers: true },
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  let observerContext: BrowserContext | undefined;
  const failures: unknown[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(`/sessions/${sessionId}`);
    await waitConnected(page);
    const body = { entityKey: '/World/Lab/Centrifuge/Rotor' };
    await expect
      .poll(() =>
        page.evaluate(
          (key) => window.__spatialDebug?.getRenderedPose(key)?.p.length ?? 0,
          body.entityKey,
        ),
      )
      .toBe(3);
    const initialRendered = await page.evaluate(
      (key) => window.__spatialDebug?.getRenderedPose(key),
      body.entityKey,
    );
    await startRotor(request, sessionId);
    await expect
      .poll(async () =>
        JSON.stringify(
          await page.evaluate(
            (key) => window.__spatialDebug?.getRenderedPose(key),
            body.entityKey,
          ),
        ),
      )
      .not.toBe(JSON.stringify(initialRendered));

    // Join through the native transport while the primary runs the real rotor program.
    observerContext = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const observer = await observerContext.newPage();
    observer.on('pageerror', error => errors.push(error.message));
    await observer.goto(`/sessions/${sessionId}`);
    await waitConnected(observer);
    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('已暂停');
    await expect(observer.getByTestId('session-state')).toHaveText('已暂停');
    await expect(
      page.getByText('命令已应用', { exact: true }),
    ).toBeVisible();
    const snapshot = (await (await request.get(`${api}/api/v1/sessions/${sessionId}/snapshot`)).json()) as Snapshot;
    // A paused observer remains healthy through multiple heartbeat periods.
    await page.waitForTimeout(3300);
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    expect(
      (
        (await (
          await request.get(`${api}/api/v1/sessions/${sessionId}/snapshot`)
        ).json()) as Snapshot
      ).simTime,
    ).toBe(snapshot.simTime);
    await page.getByRole('button', { name: 'Step simulation' }).click();
    await expect
      .poll(() =>
        page.evaluate(() => window.__spatialDebug?.getSample()?.seq ?? -1),
      )
      .toBeGreaterThan(snapshot.seq);
    const stepped = (await (
      await request.get(`${api}/api/v1/sessions/${sessionId}/snapshot`)
    ).json()) as Snapshot;
    expect(stepped.simTime).toBeGreaterThan(snapshot.simTime);
    await expect(observer.getByTestId('sequence')).toHaveText(
      String(stepped.seq),
    );
    await expect.poll(() => observer.evaluate(() => {
      const sample = window.__spatialDebug?.getSample();
      return sample && { seq: sample.seq, simTime: sample.simTime };
    })).toEqual({ seq: stepped.seq, simTime: stepped.simTime });

    await resetAndResume(page, observer, snapshot);
    expect(errors).toEqual([]);
  } catch (error) {
    failures.push(error);
  } finally {
    await cleanupObserver(observerContext, [`${api}/api/v1/sessions/${sessionId}`], failures);
  }
});

test('real SyntheticSource observers reconnect after Reset and expose fallback diagnostics before closing', async ({ page, browser, request }, testInfo) => {
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneVersion: 'lab-demo@1', source: 'synthetic', continueWithoutObservers: true },
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  let observerContext: BrowserContext | undefined;
  const failures: unknown[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(`/sessions/${sessionId}`);
    await waitConnected(page);
    observerContext = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const observer = await observerContext.newPage();
    observer.on('pageerror', error => errors.push(error.message));
    const observerSockets: WebSocketRoute[] = [];
    let blockObserverNetwork = false;
    await observer.routeWebSocket('**/api/v1/sessions/*/ws', (route) => {
      if (blockObserverNetwork)
        return route.close({ code: 4001, reason: 'Network-loss test' });
      route.connectToServer();
      observerSockets.push(route);
    });
    await observer.goto(`/sessions/${sessionId}`);
    await waitConnected(observer);
    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('已暂停');
    await expect(observer.getByTestId('session-state')).toHaveText('已暂停');
    await expect(
      page.getByText('命令已应用', { exact: true }),
    ).toBeVisible();
    const snapshot = (await (await request.get(`${api}/api/v1/sessions/${sessionId}/snapshot`)).json()) as Snapshot;
    // Reconnect from a resumed, nonzero epoch after both observers witnessed Reset.
    await resetAndResume(page, observer, snapshot);
    // Chromium's HTTP offline emulation preserves existing WebSockets. Close the
    // real upstream relay instead; all frames still originate from the gateway.
    blockObserverNetwork = true;
    await observerSockets
      .at(-1)!
      .close({ code: 4001, reason: 'Network-loss test' });
    await expect(observer.getByTestId('connection-status')).not.toHaveText(
      '已连接',
    );
    blockObserverNetwork = false;
    await waitConnected(observer);
    await expect
      .poll(async () => {
        const current = await observer.evaluate(
          () => window.__spatialDebug?.getSample()?.seq ?? -1,
        );
        const latest = await page.evaluate(
          () => window.__spatialDebug?.getSample()?.seq ?? -1,
        );
        return Math.abs(current - latest);
      })
      .toBeLessThan(10);

    await page.getByRole('button', { name: /Diagnostics/ }).click();
    await expect(page.getByText('Compatibility & asset warnings')).toBeVisible();
    await expect(page.getByText(/Asset unavailable:/).first()).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath('workbench-diagnostics.png'),
    });
    expect(errors).toEqual([]);
    await page
      .getByRole('button', { name: 'Close session', exact: true })
      .click();
    await expect(page.getByRole('link', { name: '打开场景库' })).toBeVisible();
    await expect(page.getByLabel('Session ID', { exact: true })).toBeVisible();
    expect(
      (await (await request.get(`${api}/api/v1/sessions/${sessionId}`)).json())
        .state,
    ).toBe('STOPPED');
  } catch (error) {
    failures.push(error);
  } finally {
    await cleanupObserver(observerContext, [`${api}/api/v1/sessions/${sessionId}`], failures);
  }
});
