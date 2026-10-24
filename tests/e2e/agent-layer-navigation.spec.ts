import { expect, test } from '@playwright/test';

for (const status of ['admitting', 'cancelling', 'failed']) {
  test(`switching from Agent ${status} preserves shared search and clears only Agent statuses`, async ({ page }) => {
    await page.goto(`/train?layer=agent&status=${status}&q=retained-query&goal=goal%2Flayer-probe#jobs`);
    await expect(page.getByLabel('筛选 Agent 运行状态')).toHaveValue(status);
    await page.getByRole('group', { name: '训练层' }).getByRole('button', { name: /^具身层/ }).click();
    const expected = status === 'failed' ? 'failed' : null;
    await expect.poll(() => new URL(page.url()).searchParams.get('layer')).toBe(null);
    await expect.poll(() => new URL(page.url()).searchParams.get('status')).toBe(expected);
    await expect(page.getByLabel('筛选作业状态')).toHaveValue(expected ?? '');
    const current = new URL(page.url());
    expect(current.searchParams.get('q')).toBe('retained-query');
    expect(current.searchParams.get('goal')).toBe('goal/layer-probe');
    expect(current.hash).toBe('#jobs');
  });
}
