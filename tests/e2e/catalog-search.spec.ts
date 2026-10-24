import { expect, test } from '@playwright/test';

const revision = '524747d9b5e1f21b5b18b56041788c01d745f1a1';

test('online catalog scope renders the five committed candidates and bilingual search', async ({ page }) => {
  const remote: string[] = [];
  page.on('request', request => {
    if (/huggingface\.co|hf\.co|cdn-lfs/.test(request.url())) remote.push(request.url());
  });
  await page.goto('/assets?scope=online');
  await expect(page.locator('.catalog-card.is-online')).toHaveCount(5);
  await expect(page.locator('.catalog-snapshot')).toContainText('sha256:');
  await expect(page.locator('.catalog-snapshot')).toContainText('Curated SimReady subset; not the complete dataset.');
  await expect(page.locator('.catalog-snapshot')).toContainText('索引时间');
  await expect(page.locator('.catalog-snapshot')).toContainText(/\d+ (秒|分钟|小时|天)前/);
  await expect(page.locator('.catalog-snapshot')).toContainText('快照可离线读取');
  await page.getByRole('searchbox', { name: '搜索素材' }).fill('纸箱');
  const cards = page.locator('.catalog-card.is-online');
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText('纸箱');
  await expect(cards.first()).toContainText('Cardboard box');
  expect(new URL(page.url()).searchParams.get('q')).toBe('纸箱');
  expect(new URL(page.url()).searchParams.get('scope')).toBe('online');
  // Explicit conditions and the URL state survive a reload without relaxing anything.
  await page.getByLabel('素材种类', { exact: true }).selectOption('environment');
  await page.getByLabel('用途', { exact: true }).selectOption('dressing');
  await page.getByRole('checkbox', { name: '允许近似结果' }).check();
  await expect(page).toHaveURL(/scope=online/);
  const url = new URL(page.url());
  expect(Object.fromEntries(url.searchParams)).toMatchObject({
    scope: 'online', q: '纸箱', kind: 'environment', usage: 'dressing', approximate: 'true',
  });
  await page.reload();
  await expect(page.getByRole('searchbox', { name: '搜索素材' })).toHaveValue('纸箱');
  await expect(page.getByLabel('素材种类', { exact: true })).toHaveValue('environment');
  await expect(page.getByLabel('用途', { exact: true })).toHaveValue('dressing');
  await expect(page.getByRole('checkbox', { name: '允许近似结果' })).toBeChecked();
  await expect(page.locator('.catalog-card.is-online')).toHaveCount(1);
  await page.getByRole('checkbox', { name: '允许近似结果' }).uncheck();
  await page.getByLabel('用途', { exact: true }).selectOption('');
  await page.getByRole('searchbox', { name: '搜索素材' }).fill('carton');
  await expect(page.locator('.catalog-card.is-online')).toHaveCount(1);
  await expect(page.locator('.catalog-card.is-online')).toContainText('纸箱');
  await page.setViewportSize({ width: 400, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
  expect(remote).toEqual([]);
});

test('all scope keeps online candidates and uncorrelated local results in one set', async ({ page }) => {
  await page.goto('/assets?scope=all');
  await expect(page.locator('.catalog-card.is-online').first()).toBeVisible();
  await expect(page.locator('.catalog-card.is-local').first()).toBeVisible();
  await expect(page.locator('.asset-library-toolbar')).toContainText('在线 5');
  await expect(page.locator('.catalog-snapshot')).toContainText('5 条');
  await page.setViewportSize({ width: 400, height: 900 });
  const [first, second] = await page.locator('.catalog-result-grid .catalog-card').evaluateAll(cards =>
    cards.slice(0, 2).map(card => {
      const bounds = card.getBoundingClientRect();
      return { x: bounds.x, y: bounds.y, bottom: bounds.bottom };
    }));
  expect(first.x).toBeCloseTo(second.x, 0);
  expect(second.y).toBeGreaterThan(first.bottom);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole('searchbox', { name: '搜索素材' }).fill('Franka');
  await expect(page.locator('.catalog-card.is-local').first()).toBeVisible();
  await expect(page.locator('.catalog-card.is-online')).toHaveCount(0);
});

test('zero results explain the applied filters and only act when the user asks', async ({ page }) => {
  await page.goto('/assets?scope=online&q=cart&usage=dressing');
  const zero = page.locator('.catalog-zero');
  await expect(zero).toBeVisible();
  await expect(zero.getByRole('heading', { name: '没有匹配的候选' })).toBeVisible();
  await expect(zero).toContainText('明确条件排除了候选');
  await expect(zero).toContainText('用途');
  await expect(zero.getByRole('button', { name: '移除条件：用途' })).toBeVisible();
  await expect(zero.getByRole('button', { name: '允许近似结果' })).toBeVisible();
  await expect(page.locator('.catalog-card')).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get('usage')).toBe('dressing');
  await zero.getByRole('button', { name: '移除条件：用途' }).click();
  await expect(page).not.toHaveURL(/usage=dressing/);
  await expect(page.locator('.catalog-card.is-online')).toHaveCount(1);
  await page.getByLabel('用途', { exact: true }).selectOption('dressing');
  await expect(page.locator('.catalog-zero')).toBeVisible();
  await page.getByRole('checkbox', { name: '允许近似结果' }).check();
  const card = page.locator('.catalog-card.is-online');
  await expect(card).toHaveCount(1);
  await expect(card).toContainText('工具推车');
  await expect(card).toContainText('Utility equipment cart');
  await expect(card).toContainText('近似');
  await expect(card.locator('.catalog-candidate-warnings')).toContainText('未知');
  await expect(card).toContainText('布景 未知');
  expect(new URL(page.url()).searchParams.get('approximate')).toBe('true');
});

test('candidate detail and estimate expose the fixed revision and desk/cart eligibility reasons', async ({ page }) => {
  await page.goto('/assets?scope=online&q=desk');
  const desk = page.locator('.catalog-card.is-online');
  await expect(desk).toHaveCount(1);
  await desk.getByRole('button', { name: '详情与估算' }).click();
  const detail = page.getByRole('region', { name: '候选详情与估算' });
  await expect(detail).toBeVisible();
  await expect(detail).toContainText(revision);
  await expect(detail).toContainText('Laboratory desk');
  await expect(detail).toContainText('128 MiB');
  await expect(detail).toContainText('SF2');
  await expect(detail).toContainText('必须磁盘');
  await expect(detail).toContainText('acquisition preflight reports reuse');
  await expect(detail).toContainText('估算不下载主文件');
  await expect(detail).toContainText('BSD-3-Clause');
  await expect(detail).toContainText('source_assets/OmniPBR.mdl');
  await expect(detail.locator('.catalog-source-link')).toHaveAttribute('href', /huggingface\.co/);
  await expect(detail.locator('img')).toHaveCount(0);
  await page.getByRole('button', { name: '关闭详情' }).click();
  await expect(detail).toHaveCount(0);
  await page.getByRole('searchbox', { name: '搜索素材' }).fill('cart');
  await expect(page.locator('.catalog-card.is-online')).toHaveCount(1);
  await page.locator('.catalog-card.is-online').getByRole('button', { name: '详情与估算' }).click();
  await expect(page.getByRole('region', { name: '候选详情与估算' })).toContainText('UDIM/UVTILE');
  await expect(page.getByRole('region', { name: '候选详情与估算' })).toContainText('不可获取');
});

test('switching back to local preserves the existing local search and zero-result behavior', async ({ page }) => {
  await page.goto('/assets');
  await expect(page.getByRole('button', { name: '本地', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.asset-card').first()).toBeVisible();
  await page.getByRole('button', { name: '在线', exact: true }).click();
  await expect(page.locator('.catalog-snapshot')).toBeVisible();
  await page.getByRole('button', { name: '本地', exact: true }).click();
  await expect(page.locator('.asset-card').first()).toBeVisible();
  expect(new URL(page.url()).searchParams.get('scope')).toBe('local');
  await page.reload();
  await expect(page.locator('.asset-card').first()).toBeVisible();
  await page.getByRole('searchbox', { name: '搜索素材' }).fill('no-such-local-asset-sf1a');
  await expect(page.getByRole('heading', { name: '没有匹配的素材' })).toBeVisible();
  await expect(page.getByRole('button', { name: '清除筛选' })).toBeVisible();
  await page.getByRole('button', { name: '清除筛选' }).click();
  await expect(page.getByRole('searchbox', { name: '搜索素材' })).toHaveValue('');
  await expect(page.locator('.asset-card').first()).toBeVisible();
});
