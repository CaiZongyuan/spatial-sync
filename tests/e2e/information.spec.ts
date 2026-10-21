import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import type {} from '../../apps/web/src/components/Workbench';
import { cleanupObserver } from './cleanup-observer';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const centrifuge = '/World/Lab/Centrifuge';

test('lab-demo version 2 shows seeded lab device readings and keeps high risk controls behind approval', async ({ page, request }, info) => {
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneVersion: 'lab-demo@2', seed: 42, approval: { high: 'require' } },
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  try {
    await page.goto(`/sessions/${sessionId}`);
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    await page.getByTestId('entity-/World/Lab/Incubator').click();
    const temperature = page.getByTestId('property-temperatureC').locator('output');
    await expect(temperature).not.toHaveText('Unknown');
    const first = await temperature.innerText();
    await expect(temperature).not.toHaveText(first);
    const setpoint = await action(page, 'setSetpoint');
    await setpoint.getByLabel('setSetpoint temperatureC', { exact: true }).fill('38');
    await setpoint.getByRole('button', { name: 'Run setSetpoint', exact: true }).click();
    await expect(invocation(page, 'setSetpoint').locator('.invocation-status')).toHaveText('pending_approval');
    await expect(page.getByTestId('property-setpointC').locator('output')).toHaveText('37');
    await page.getByTestId('entity-/World/Lab/BiosafetyCabinet').click();
    await expect(page.getByTestId('property-inflowMs').locator('output')).not.toHaveText('Unknown');
    await expect(page.getByTestId('action-setUv')).toContainText('Approval may be required');
    await page.getByTestId('entity-/World/Lab/EnvironmentSensor').click();
    await expect(page.getByTestId('property-humidityPercent').locator('output')).not.toHaveText('Unknown');
    await expect.poll(() => page.evaluate(() => ['/World/Lab/Incubator', '/World/Lab/BiosafetyCabinet',
      '/World/Lab/EnvironmentSensor'].every(key => window.__spatialRenderDebug?.drawnEntities().includes(key)))).toBe(true);
    await page.screenshot({ path: info.outputPath('virtual-lab-devices.png') });
  } finally { await request.delete(`${api}/api/v1/sessions/${sessionId}`); }
});

async function device(page: Page, sessionId: string) {
  await page.goto(`/sessions/${sessionId}`);
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');
  await page.getByTestId(`entity-${centrifuge}`).click();
  await expect(page.getByTestId('entity-panel')).toBeVisible();
  await expect(
    page.getByTestId('property-phase').locator('output'),
  ).not.toHaveText('Unknown');
}
async function action(page: Page, name: string) {
  const card = page.getByTestId(`action-${name}`);
  if (!(await card.evaluate((element) => (element as HTMLDetailsElement).open)))
    await card.locator('summary').click();
  return card.getByRole('form', { name: `Action ${name}` });
}
const invocation = (page: Page, name: string) =>
  page
    .getByRole('region', { name: 'Invocations' })
    .locator('.invocation-row')
    .filter({ has: page.getByText(name, { exact: true }) })
    .first();

test('Inspector schema forms reject an action with unmet preconditions', async ({ page, request }) => {
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneId: 'lab-demo', source: 'synthetic' },
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  const errors: string[] = [];
  const failures: unknown[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await device(page, sessionId);
    const start = await action(page, 'start');
    await expect(start.getByRole('list', { name: 'start preconditions' }).locator('.unmet')).not.toHaveCount(0);
    await start.getByLabel('start rpm', { exact: true }).fill('3000');
    await start.getByLabel('start durationSeconds', { exact: true }).fill('2');
    await start.getByRole('button', { name: 'Run start', exact: true }).click();
    await expect(invocation(page, 'start').locator('.invocation-status')).toHaveText('rejected');
    await expect(start.getByRole('status')).toContainText('precondition_failed');
    expect(errors).toEqual([]);
  } catch (error) {
    failures.push(error);
  } finally {
    await cleanupObserver(undefined, [`${api}/api/v1/sessions/${sessionId}`], failures);
  }
});

test('Inspector schema forms run a centrifuge action and two observers see its lifecycle', async ({
  page,
  browser,
  request,
}, info) => {
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneId: 'lab-demo', source: 'synthetic' },
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  let context: BrowserContext | undefined;
  const failures: unknown[] = [];
  try {
    context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const observer = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    observer.on('pageerror', (error) => errors.push(error.message));
    await Promise.all([device(page, sessionId), device(observer, sessionId)]);
    const start = await action(page, 'start');
    await (await action(page, 'closeLid'))
      .getByRole('button', { name: 'Run closeLid', exact: true })
      .click();
    await expect(
      invocation(page, 'closeLid').locator('.invocation-status'),
    ).toHaveText('succeeded');
    for (const view of [page, observer])
      await expect(
        view.getByTestId('property-lidOpen').locator('output'),
      ).toHaveText('false');
    await expect(
      start
        .getByRole('list', { name: 'start preconditions' })
        .locator('.unmet'),
    ).toHaveCount(0);
    await start.getByLabel('start rpm', { exact: true }).fill('3000');
    await start.getByLabel('start durationSeconds', { exact: true }).fill('2');
    await start.getByRole('button', { name: 'Run start', exact: true }).click();
    await expect(
      invocation(page, 'start').locator('.invocation-status'),
    ).toHaveText('running');
    const id = (await invocation(page, 'start').getAttribute('data-testid'))!;
    await expect(
      observer.getByTestId(id).locator('.invocation-status'),
    ).toHaveText('running');
    const canvas = await page.locator('canvas').boundingBox();
    await expect(page.getByTestId(`phase-${centrifuge}`)).toHaveText(
      /accelerating|running|decelerating/,
    );
    expect(canvas!.width).toBeGreaterThan(500);
    expect(canvas!.height).toBeGreaterThan(300);
    expect(
      await page.evaluate(
        (box) =>
          document.elementFromPoint(
            box.x + box.width / 2,
            box.y + box.height / 2,
          )?.tagName,
        canvas!,
      ),
    ).toBe('CANVAS');
    await page.screenshot({
      path: info.outputPath('device-action-running.png'),
    });
    await expect(
      invocation(page, 'start').locator('.invocation-status'),
    ).toHaveText('succeeded', { timeout: 20_000 });
    await expect(
      observer.getByTestId(id).locator('.invocation-status'),
    ).toHaveText('succeeded');
    for (const view of [page, observer])
      await expect(
        view.getByTestId('property-phase').locator('output'),
      ).toHaveText('idle');
    await invocation(page, 'start').locator('summary').click();
    await expect(
      invocation(page, 'start').locator('.invocation-details'),
    ).toContainText('3000');
    await page.getByLabel('Filter invocations').selectOption('selected');
    await page.getByTestId('property-phase').scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath('device-properties-and-invocations.png'),
    });
    expect(errors).toEqual([]);
  } catch (error) {
    failures.push(error);
  } finally {
    await cleanupObserver(context, [`${api}/api/v1/sessions/${sessionId}`], failures);
  }
});

test('an operator can cancel a running invocation from its details', async ({
  page,
  request,
}) => {
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneId: 'lab-demo', source: 'synthetic' },
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  try {
    await device(page, sessionId);
    await (await action(page, 'closeLid'))
      .getByRole('button', { name: 'Run closeLid', exact: true })
      .click();
    await expect(
      invocation(page, 'closeLid').locator('.invocation-status'),
    ).toHaveText('succeeded');
    const start = await action(page, 'start');
    await start.getByLabel('start rpm', { exact: true }).fill('500');
    await start.getByLabel('start durationSeconds', { exact: true }).fill('20');
    await start.getByRole('button', { name: 'Run start', exact: true }).click();
    await expect(
      invocation(page, 'start').locator('.invocation-status'),
    ).toHaveText('running');
    await invocation(page, 'start').locator('summary').click();
    await invocation(page, 'start')
      .getByRole('button', { name: 'Cancel start', exact: true })
      .click();
    await expect(
      invocation(page, 'start').locator('.invocation-status'),
    ).toHaveText('cancelled');
    await expect(
      page.getByTestId('property-phase').locator('output'),
    ).toHaveText('idle');
  } finally {
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});

test('an operator can deny and approve a declared high risk action', async ({
  page,
  request,
}, info) => {
  test.skip(
    process.env.SPATIAL_TEST_APPROVAL !== '1',
    'Requires the temporary high risk definition fixture',
  );
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: {
      sceneId: 'lab-demo',
      source: 'synthetic',
      approval: { high: 'require' },
    },
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  try {
    await device(page, sessionId);
    const close = await action(page, 'closeLid');
    await close
      .getByRole('button', { name: 'Run closeLid', exact: true })
      .click();
    const approvals = page.getByRole('region', { name: 'Pending approvals' });
    await expect(approvals).toBeVisible();
    await expect(
      invocation(page, 'closeLid').locator('.invocation-status'),
    ).toHaveText('pending_approval');
    await page.screenshot({
      path: info.outputPath('operator-approval-banner.png'),
    });
    await approvals
      .getByRole('button', { name: 'Deny closeLid', exact: true })
      .click();
    await expect(
      invocation(page, 'closeLid').locator('.invocation-status'),
    ).toHaveText('rejected');
    await expect(close.getByRole('status')).toContainText('approval_denied');
    await expect(
      page.getByTestId('property-lidOpen').locator('output'),
    ).toHaveText('true');
    await close
      .getByRole('button', { name: 'Run closeLid', exact: true })
      .click();
    await expect(approvals).toBeVisible();
    await approvals
      .getByRole('button', { name: 'Approve closeLid', exact: true })
      .click();
    await expect(
      invocation(page, 'closeLid').locator('.invocation-status'),
    ).toHaveText('succeeded');
    await expect(approvals).toHaveCount(0);
    await expect(
      page.getByTestId('property-lidOpen').locator('output'),
    ).toHaveText('false');
  } finally {
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});

test('schema string fields preserve JSON Schema Unicode and pattern semantics', async ({
  page,
  request,
}) => {
  test.skip(
    process.env.SPATIAL_TEST_SCHEMA_FIELDS !== '1',
    'Requires the temporary schema annotation fixture',
  );
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneId: 'lab-demo', source: 'synthetic' },
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  try {
    await page.goto(`/sessions/${sessionId}`);
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    await page.getByTestId('entity-/World/Lab/RobotA').click();
    const move = await action(page, 'moveJoints');
    await move
      .getByLabel('moveJoints positions', { exact: true })
      .fill('[0,0,0,0,0,0,0]');
    await move.getByLabel('moveJoints note', { exact: true }).fill('😀');
    await move.getByLabel('moveJoints code', { exact: true }).fill('a');
    await move
      .getByRole('button', { name: 'Run moveJoints', exact: true })
      .click();
    await expect(
      invocation(page, 'moveJoints').locator('.invocation-status'),
    ).toHaveText('succeeded');
    await invocation(page, 'moveJoints').locator('summary').click();
    await expect(
      invocation(page, 'moveJoints').locator('.invocation-details'),
    ).toContainText('😀');
    await expect(
      invocation(page, 'moveJoints').locator('.invocation-details'),
    ).toContainText('"label": ""');
  } finally {
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});
