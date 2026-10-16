import { expect, test, type Page } from '@playwright/test';
import type {} from '../../apps/web/src/components/Workbench';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const centrifuge = '/World/Lab/Centrifuge';

async function device(page: Page, sessionId: string) {
  await page.goto(`/sessions/${sessionId}`);
  await expect(page.getByTestId('connection-status')).toHaveText('connected');
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
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
  });
  const observer = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  observer.on('pageerror', (error) => errors.push(error.message));
  try {
    await device(page, sessionId);
    await device(observer, sessionId);
    const start = await action(page, 'start');
    await expect(
      start
        .getByRole('list', { name: 'start preconditions' })
        .locator('.unmet'),
    ).not.toHaveCount(0);
    await start.getByLabel('start rpm', { exact: true }).fill('3000');
    await start.getByLabel('start durationSeconds', { exact: true }).fill('2');
    await start.getByRole('button', { name: 'Run start', exact: true }).click();
    await expect(invocation(page, 'start').locator('.invocation-status')).toHaveText('rejected');
    await expect(start.getByRole('status')).toContainText('precondition_failed');
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
    await expect(page.getByTestId(`phase-${centrifuge}`)).toHaveText(/accelerating|running|decelerating/);
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
  } finally {
    await context.close();
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
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
