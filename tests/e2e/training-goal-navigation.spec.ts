import { expect, test, type Page } from '@playwright/test';

const goal = 'goal/delayed-navigation-probe';

async function delayGoalDiscovery(page: Page) {
  let release!: () => void, started!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { started = resolve; });
  await page.route(url => url.pathname === '/api/v1/loops' && url.searchParams.get('layer') === 'embodied', async route => {
    started(); await held;
    await route.fulfill({ json: { goals: [goal] } });
  });
  return { release, requested };
}

test('delayed Goal discovery preserves the current policy hash and Training filters', async ({ page }) => {
  const discovery = await delayGoalDiscovery(page);
  try {
    await page.goto('/train?q=owned-navigation&status=queued');
    await discovery.requested;
    await page.getByRole('navigation', { name: '具身层阶段' }).getByRole('link', { name: /控制器版本/ }).click();
    await expect(page).toHaveURL(/#policies$/);
    const before = new URL(page.url());
    discovery.release();
    await expect(page.getByLabel('选择改进目标')).toHaveValue(goal);
    const current = new URL(page.url());
    expect(current.searchParams.get('goal')).toBe(goal);
    expect(current.searchParams.get('q')).toBe(before.searchParams.get('q'));
    expect(current.searchParams.get('status')).toBe(before.searchParams.get('status'));
    expect(current.hash).toBe('#policies');
  } finally { discovery.release(); }
});

test('a completed Goal response cannot reopen Training after the panel unmounts', async ({ page }) => {
  // Model an already-settling transport: completion can still arrive after its
  // owner aborts. The real browser fetch and response parsing remain in use.
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = async (input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      if (url.pathname !== '/api/v1/loops') return original(input, init);
      const response = await original(input, { ...init, signal: undefined });
      await response.clone().json();
      (window as Window & { goalDiscoveryCompleted?: boolean }).goalDiscoveryCompleted = true;
      return response;
    };
  });
  const discovery = await delayGoalDiscovery(page);
  try {
    await page.goto('/train'); await discovery.requested;
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Resources', exact: true }).click();
    await expect(page).toHaveURL(/\/resources\/gpu$/);
    discovery.release();
    await page.waitForFunction(() => (window as Window & { goalDiscoveryCompleted?: boolean }).goalDiscoveryCompleted);
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page).toHaveURL(/\/resources\/gpu$/);
    await expect(page.getByTestId('goal-notebook-panel')).toHaveCount(0);
  } finally { discovery.release(); }
});
