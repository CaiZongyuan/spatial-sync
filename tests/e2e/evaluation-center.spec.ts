import { expect, test } from '@playwright/test';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

declare global { interface Window { __evalRequests: { active: number; peak: number; batches: number; aborted: number; hold: boolean } } }

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
  await expect(page.locator('[data-suite-id="suite/lab-ops-basic@1"]')).toContainText('10 个 train 题目');
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

test('real Agent Suite exposes public tasks, external evaluation inputs and a locked test split', async ({ page, request }, info) => {
  const response = await request.get(`${api}/api/v1/suites/lab-ops-basic/1`);
  expect(response.ok(), await response.text()).toBe(true);
  const suite = await response.json();
  expect(suite).toMatchObject({ id: 'suite/lab-ops-basic@1', layer: 'agent', scene: 'lab-demo@3', runsPerCase: 3 });
  expect(suite.cases).toHaveLength(10);
  const agent = await (await request.get(`${api}/api/v1/agents/lab-operator`)).json();
  expect(agent).toMatchObject({ id: suite.target, model: null, thinking: null, referenceVersion: 1 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/eval');
  await page.getByRole('button', { name: 'Agent', exact: true }).click();
  const card = page.locator(`[data-suite-id="${suite.id}"]`);
  await expect(card).toContainText('10 个 train 题目');
  await expect(card).toContainText(suite.target);
  await expect(card).toContainText('尚无正式基线');
  await expect(card).toContainText('外部 runtime 交接');
  await card.getByRole('link', { name: '打开计分板' }).click();
  await expect.poll(() => decodeURIComponent(new URL(page.url()).pathname)).toBe('/eval/lab-ops-basic@1');
  await expect(page.getByRole('heading', { name: suite.title, exact: true })).toBeVisible();
  await expect(page.getByRole('table', { name: 'Agent 评估计分板', exact: true })).toContainText('尚无完整有效的 Agent train 运行记录');
  await expect(page.getByRole('heading', { name: '创建 Agent 评估', exact: true })).toBeVisible();
  await expect(page.getByLabel('评估 Harness 版本')).toContainText('v1');
  await expect(page.getByLabel('模型声明')).toHaveValue('');
  await expect(page.getByTestId('held-out-test-row')).toContainText('锁定');
  const cases = page.locator('.eval-agent-cases details');
  await expect(cases).toHaveCount(10);
  for (const item of suite.cases) await expect(page.locator('.eval-agent-cases')).toContainText(item.id);
  await cases.first().locator('summary').click();
  await expect(cases.first()).toContainText(suite.cases[0].statement.trim());
  await expect(cases.first().locator('p')).toBeVisible();
  await expect(page.locator('.eval-chart-line')).toHaveCount(0);
  await expect(page.locator('.evaluation-center')).not.toContainText('held-out-safe-sensor');
  await expect(page.locator('.evaluation-center')).not.toContainText('此层的题目与运行器尚未接入');
  await info.attach('real-agent-evaluation-center.png', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
  expect(errors).toEqual([]);
});

test('condition fixture with identical public tags keeps separate curves and surfaces not_comparable', async ({ page, request }) => {
  // The metadata is the real Suite; these score records are explicitly contract fixtures.
  const suite = await (await request.get(`${api}/api/v1/suites/lab-demo-pick-place/1`)).json();
  const key = { suite: suite.id, scene: suite.scene, source: suite.source, target: suite.target };
  const baseline = { evaluationId: 'fixture-baseline', suite: suite.id, subject: 'robot_arm.virtual', version: 'fixture-runtime-a',
    key, conditionsGroup: 'fixture-baseline', comparisonState: 'known', split: 'train', successes: 1, episodes: 2, score: .5, ci95: [.09, .91],
    decision: 'baseline', reference: null, phases: { grasp: { passed: 1, episodes: 2, rate: .5 }, place: { passed: 1, episodes: 2, rate: .5 } },
    recordings: [], durationSeconds: 4, at: '2026-10-23T00:00:00Z' };
  const candidate = { ...baseline, evaluationId: 'fixture-candidate', subject: 'policy/fixture@1', version: 'fixture-runtime-b', score: 1, successes: 2,
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
  await expect(page.locator('.eval-curves figure').first().locator('figcaption')).toContainText(candidate.subject);
  await expect(page.locator('.eval-curves circle title').filter({ hasText: candidate.evaluationId })).toContainText(candidate.subject);
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

test('failed public read can retry and an Agent Suite without runs stays readable', async ({ page }) => {
  const suite = { id: 'suite/future-agent@1', title: 'Agent layer contract fixture', layer: 'agent',
    contentHash: 'fixture-suite-hash', scene: 'lab-demo@3', sceneContentHash: 'fixture-scene-hash', source: 'synthetic',
    target: 'agent/fixture-operator', split: 'train', runsPerCase: 3, cases: [{ id: 'public-task', statement: 'Public task statement.' }],
    gradingDimensions: ['finalState'] };
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
  await expect(page.getByText('尚无完整有效的 Agent train 运行记录。', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: '角色与可见性', exact: true })).toBeVisible();
  await expect(page.getByTestId('held-out-test-row')).toContainText('锁定');
  expect(errors).toEqual([]);
});

test('controlled invalid attempt stays visible without a score and legacy conditions stay unknown', async ({ page, request }) => {
  const suite = await (await request.get(`${api}/api/v1/suites/lab-demo-pick-place/1`)).json();
  const legacy = { evaluationId: 'fixture-legacy', suite: suite.id, subject: 'robot_arm.virtual', version: 'old-code',
    key: { scene: suite.scene, source: suite.source }, conditionsGroup: 'fixture-legacy', comparisonState: 'unknown',
    split: 'train', successes: 1, episodes: 2, score: .5, ci95: [.09, .91], decision: 'baseline', reference: null,
    phases: {}, recordings: [], durationSeconds: 4, at: '2026-10-23T00:00:00Z' };
  const invalid = { evaluationId: 'fixture-invalid', suite: suite.id, subject: legacy.subject, status: 'failed',
    at: '2026-10-23T00:01:00Z', attemptedEpisodes: 1, error: { code: 'evaluation_observation_gap' }, episodes: [{
      caseId: 'train-one', status: 'invalid', reason: 'evaluation_observation_gap', milestones: { grasp: true },
      recordingId: 'fixture-partial-recording', invocationId: 'fixture-invocation', cleanupComplete: false,
    }] };
  await page.route('**/api/v1/evaluations', route => route.fulfill({ json: { evaluations: [
    { evaluationId: legacy.evaluationId, suite: suite.id, subject: legacy.subject, status: 'completed', at: legacy.at, episodes: [], result: legacy }, invalid,
  ] } }));
  await page.route('**/api/v1/suites/lab-demo-pick-place/1/scoreboard', route => route.fulfill({ json: { rows: [legacy] } }));
  await page.goto('/eval/lab-demo-pick-place@1');
  await expect(page.locator('[data-evaluation-id="fixture-legacy"]')).not.toContainText('当前参照');
  await expect(page.locator('.eval-curves figcaption')).toContainText('条件未知');
  await expect(page.getByRole('region', { name: '可比较条件', exact: true })).toContainText('比较证据未知');
  await page.getByLabel('评估明细', { exact: true }).selectOption(invalid.evaluationId);
  const attempt = page.getByRole('table', { name: '评估回合', exact: true }).getByRole('row').filter({ hasText: 'train-one' });
  await expect(attempt.getByRole('cell', { name: '实验无效', exact: false })).toContainText('evaluation_observation_gap');
  await expect(attempt).toContainText('通过');
  await expect(attempt).toContainText('未确认');
  await expect(attempt.getByRole('link', { name: '查看回放 ↗' })).toHaveAttribute('href', '/replay/fixture-partial-recording');
  await expect(page.locator('[data-evaluation-id="fixture-invalid"]')).toHaveCount(0);
  await expect(page.locator('.eval-selected-id')).toHaveCount(0);
});

test('partial failure cancels deferred peers before polling and navigation aborts the pending batch', async ({ page }) => {
  await page.clock.install();
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    const probe = window.__evalRequests = { active: 0, peak: 0, batches: 0, aborted: 0, hold: false };
    window.fetch = (input, options) => {
      const path = new URL(input instanceof Request ? input.url : String(input), location.href).pathname;
      if (!['/api/v1/suites', '/api/v1/evaluations'].includes(path)) return original(input, options);
      if (path.endsWith('/suites')) {
        probe.batches++;
        if (!probe.hold) return Promise.resolve(new Response(JSON.stringify({ detail: { code: 'suite_unavailable' } }), { status: 503 }));
      }
      probe.active++; probe.peak = Math.max(probe.peak, probe.active);
      return new Promise<Response>((_resolve, reject) => options!.signal!.addEventListener('abort', () => {
        probe.active--; probe.aborted++; reject(options!.signal!.reason);
      }, { once: true }));
    };
  });
  await page.goto('/eval');
  await expect(page.getByRole('alert').filter({ hasText: 'suite_unavailable' })).toBeVisible();
  await expect(page.getByRole('alert')).not.toContainText('超时');
  await expect.poll(() => page.evaluate(() => ({ active: window.__evalRequests.active, aborted: window.__evalRequests.aborted }))).toEqual({ active: 0, aborted: 1 });
  await page.clock.fastForward(5000);
  await expect.poll(() => page.evaluate(() => ({ active: window.__evalRequests.active, batches: window.__evalRequests.batches }))).toEqual({ active: 0, batches: 2 });
  await page.evaluate(() => { window.__evalRequests.hold = true; });
  await page.clock.fastForward(5000);
  await expect.poll(() => page.evaluate(() => window.__evalRequests.active)).toBe(2);
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Scenes', exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__evalRequests.active)).toBe(0);
  await page.clock.fastForward(10000);
  expect(await page.evaluate(() => ({ batches: window.__evalRequests.batches, peak: window.__evalRequests.peak, aborted: window.__evalRequests.aborted })))
    .toEqual({ batches: 3, peak: 2, aborted: 4 });
});
