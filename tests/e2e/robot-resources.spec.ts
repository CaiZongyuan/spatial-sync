import { expect, test } from '@playwright/test';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('real FR3 geometry, exact scene binding and honest controller history open from resource navigation', async ({ page, request }, info) => {
  const initial = await (await request.get(`${api}/api/v1/health`)).json();
  const response = await request.get(`${api}/api/v1/embodiments`);
  expect(response.ok(), await response.text()).toBe(true);
  const values = (await response.json()).embodiments;
  const sim = values.find((value: { id: string }) => value.id === 'fr3-sim');
  expect(sim.currentController).toBeNull(); expect(sim.controllerHistory).toEqual([]);
  const errors: string[] = [], mutations: string[] = [], external: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.method() !== 'GET') mutations.push(`${request.method()} ${new URL(request.url()).pathname}`);
    if (/^https?:/.test(request.url()) && !['127.0.0.1', 'localhost'].includes(new URL(request.url()).hostname)) external.push(request.url());
  });
  await page.goto('/resources/gpu');
  await page.getByRole('navigation', { name: '资源标签', exact: true }).locator('a[href="/resources/robots"]').click();
  await expect(page).toHaveURL(/\/resources\/robots$/);
  await expect(page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Resources', exact: true })).toHaveAttribute('aria-current', 'page');
  const simulated = page.locator('[data-embodiment="fr3-sim"]'), real = page.locator('[data-embodiment="fr3-real"]');
  await expect(simulated).toContainText('/World/Lab/RobotA');
  await expect(simulated).toContainText('robot-and-tubes@2');
  await expect(simulated).toContainText('robot_arm.newton'); await expect(simulated).toContainText('graspMode: weld');
  await expect(simulated).toContainText('尚无已提升 Controller'); await expect(simulated).toContainText('尚无已提升版本');
  await expect(simulated.locator('[aria-label="素材静态模型预览"]')).toHaveAttribute('data-model-state', 'ready');
  expect(Number(await simulated.locator('[aria-label="素材静态模型预览"]').getAttribute('data-mesh-count'))).toBeGreaterThan(0);
  await expect(simulated.getByRole('link', { name: 'robot/fr3@1 ↗', exact: true })).toHaveAttribute('href', '/assets/robot/fr3/1');
  await expect(real).toContainText('fr3-real'); await expect(real).toContainText('无场景或实况绑定');
  await expect(real.locator('canvas')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('robot-resources-light.png'), fullPage: true });
  await page.getByLabel('Color theme').selectOption('dark');
  await page.setViewportSize({ width: 900, height: 1000 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('robot-resources-dark-900.png'), fullPage: true });
  await page.getByRole('navigation', { name: '资源标签', exact: true }).locator('a[href="/resources/models"]').click();
  const catalog = page.getByRole('table', { name: '机器人模型目录', exact: true });
  for (const identifier of ['openvla-7b', 'smolvla-base', 'groot-n1-5-3b']) {
    const row = catalog.locator(`[data-robot-model="${identifier}"]`);
    await expect(row).toContainText('仅登记 · v1 不推理'); await expect(row).toContainText('无平台绑定');
    expect(await row.getByRole('link').count()).toBeGreaterThan(0);
  }
  await expect(catalog.locator('[data-robot-model="groot-n1-5-3b"]')).toContainText('non-commercial');
  await expect(catalog.locator('[data-robot-model="fr3-taught-motion"]')).toContainText('支持加载');
  await expect(catalog.locator('[data-robot-model="fr3-taught-motion"]')).toContainText('质量未评估');
  await page.screenshot({ path: info.outputPath('robot-models-catalog-dark.png'), fullPage: true });
  const final = await (await request.get(`${api}/api/v1/health`)).json();
  expect(final.sessionCount).toBe(initial.sessionCount);
  expect(errors).toEqual([]); expect(mutations).toEqual([]); expect(external).toEqual([]);
  await info.attach('robot-resources-scope.json', { contentType: 'application/json', body: JSON.stringify({
    scope: 'Real metadata and local FR3 GLB; no Source, model inference, weights download, promotion or policy-quality claim',
    before: initial.sessionCount, after: final.sessionCount, mutations, external, errors,
  }) });
});
