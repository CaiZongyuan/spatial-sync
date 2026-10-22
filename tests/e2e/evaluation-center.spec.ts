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
  await expect(page.getByRole('heading', { name: '角色与可见性', exact: true })).toBeVisible();
  await info.attach('real-embodied-evaluation-center.png', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
  expect(errors).toEqual([]);
});

test('condition fixture with identical public tags keeps separate curves and surfaces not_comparable', async ({ page, request }) => {
  // The metadata is the real Suite; these score records are explicitly contract fixtures.
  const suite = await (await request.get(`${api}/api/v1/suites/lab-demo-pick-place/1`)).json();
  const key = { suite: suite.id, scene: suite.scene, source: suite.source, target: suite.target };
  const baseline = { evaluationId: 'fixture-baseline', suite: suite.id, subject: 'robot_arm.virtual', version: 'fixture-runtime-a',
    key, conditionsGroup: 'fixture-baseline', split: 'train', successes: 1, episodes: 2, score: .5, ci95: [.09, .91],
    decision: 'baseline', reference: null, phases: { grasp: { passed: 1, episodes: 2, rate: .5 }, place: { passed: 1, episodes: 2, rate: .5 } },
    recordings: [], durationSeconds: 4, at: '2026-10-23T00:00:00Z' };
  const candidate = { ...baseline, evaluationId: 'fixture-candidate', version: 'fixture-runtime-b', score: 1, successes: 2,
    decision: 'rejected', reference: 'fixture-baseline', at: '2026-10-23T00:01:00Z' };
  const different = { ...baseline, evaluationId: 'fixture-other-conditions', conditionsGroup: 'fixture-other-conditions', at: '2026-10-23T00:02:00Z' };
  const rows = [baseline, candidate, different];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/evaluations', route => route.fulfill({ json: { evaluations: rows.map(result => ({
    evaluationId: result.evaluationId, suite: suite.id, subject: result.subject, status: 'completed', at: result.at, episodes: [], result,
  })) } }));
  await page.route('**/api/v1/suites/lab-demo-pick-place/1/scoreboard*', route => {
    const params = new URL(route.request().url()).searchParams;
    return params.has('compare') ? route.fulfill({ status: 409, json: { detail: { code: 'not_comparable' } } }) : route.fulfill({ json: { rows } });
  });
  await page.goto('/eval/lab-demo-pick-place@1');
  await expect(page.locator('.eval-chart-line')).toHaveCount(2);
  await expect(page.locator('.eval-curves figure').first().locator('circle')).toHaveCount(2);
  await expect(page.locator('[data-evaluation-id="fixture-candidate"]')).not.toContainText('当前参照');
  await expect(page.locator('[data-evaluation-id="fixture-baseline"]')).toContainText('当前参照');
  await page.getByRole('combobox', { name: '比较候选', exact: true }).selectOption('fixture-other-conditions');
  await page.getByRole('combobox', { name: '比较对照', exact: true }).selectOption('fixture-baseline');
  const comparison = page.waitForRequest(request => request.url().includes('compare=fixture-other-conditions'));
  await page.getByRole('button', { name: '比较', exact: true }).click();
  expect(new URL((await comparison).url()).searchParams.get('against')).toBe('fixture-baseline');
  await expect(page.getByText('not_comparable · 完整条件不同，不能比较。', { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('failed public read can retry and an unsupported Agent Suite stays readable', async ({ page }) => {
  const suite = { id: 'suite/future-agent@1', title: 'Agent layer contract fixture', layer: 'agent' };
  let unavailable = true;
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/v1/suites', route => unavailable
    ? route.fulfill({ status: 503, json: { detail: { code: 'suite_unavailable' } } })
    : route.fulfill({ json: { suites: [suite] } }));
  await page.route('**/api/v1/evaluations', route => route.fulfill({ json: { evaluations: [] } }));
  await page.route('**/api/v1/suites/future-agent/1', route => route.fulfill({ json: suite }));
  await page.route('**/api/v1/suites/future-agent/1/scoreboard', route => route.fulfill({ json: { rows: [] } }));
  await page.goto('/eval');
  await expect(page.getByRole('alert').filter({ hasText: 'suite_unavailable' })).toBeVisible();
  unavailable = false;
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await page.getByRole('link', { name: '打开计分板' }).click();
  await expect(page.getByText('Agent 层题目与运行器尚未接入，暂无可展示的计分板。', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: '角色与可见性', exact: true })).toBeVisible();
  await expect(page.getByTestId('held-out-test-row')).toHaveCount(0);
  expect(errors).toEqual([]);
});
