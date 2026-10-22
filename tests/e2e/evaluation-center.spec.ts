import { expect, test } from '@playwright/test';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('real embodied Suite opens from Evaluation navigation with public conditions and locked test', async ({ page, request }, info) => {
  const response = await request.get(`${api}/api/v1/suites/lab-demo-pick-place/1`);
  expect(response.ok(), await response.text()).toBe(true);
  const suite = await response.json();
  expect(suite).toMatchObject({ id: 'suite/lab-demo-pick-place@1', layer: 'embodied', scene: 'lab-demo@3', source: 'synthetic' });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/eval');
  await expect(page.getByRole('link', { name: 'Evaluation', exact: true })).toHaveAttribute('aria-current', 'page');
  const card = page.locator(`[data-suite-id="${suite.id}"]`);
  await expect(card).toContainText(suite.title);
  await expect(card).toContainText(`${suite.episodes.length} 个 train 回合`);
  await page.getByRole('button', { name: 'Agent', exact: true }).click();
  await expect(page.getByText('Agent 层评估集与运行器尚未接入。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '具身', exact: true }).click();
  await card.getByRole('link', { name: '打开计分板' }).click();
  await expect.poll(() => decodeURIComponent(new URL(page.url()).pathname)).toBe('/eval/lab-demo-pick-place@1');
  await expect(page.getByRole('heading', { name: suite.title, exact: true })).toBeVisible();
  const conditions = page.getByRole('region', { name: '可比较条件', exact: true });
  await expect(conditions).toContainText(suite.scene);
  await expect(conditions).toContainText(suite.source);
  await expect(conditions).toContainText(suite.target);
  await expect(page.getByTestId('held-out-test-row')).toBeVisible();
  await expect(page.getByTestId('held-out-test-row')).toContainText('锁定');
  await expect(page.getByRole('table', { name: '具身评估计分板', exact: true })).toBeVisible();
  await page.getByText('公开 train 回合与覆盖', { exact: true }).click();
  for (const episode of suite.episodes) await expect(page.locator('.eval-public-cases')).toContainText(episode.id);
  await expect(page.locator('.evaluation-center')).not.toContainText('held-out-example');
  await expect(page.locator('.evaluation-center')).not.toContainText('9101');
  await expect(page.getByRole('heading', { name: '角色与可见性', exact: true })).toBeVisible();
  await info.attach('real-embodied-evaluation-center.png', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
  expect(errors).toEqual([]);
});
