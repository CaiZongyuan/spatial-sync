import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('real model resources mask a local credential and persist audited human price edits', async ({ page, request }, testInfo) => {
  const project = resolve('../..');
  const configuredRoot = process.env.SPATIAL_STATE_ROOT;
  if (!configuredRoot) throw new Error('Set SPATIAL_STATE_ROOT for both the owned API and E2E; never seed the user Vault');
  const suffix = randomUUID().slice(0, 8);
  const credential = `e2e/fake-${suffix}`;
  const sentinel = `FAKE-VALUE-${randomUUID()}`;
  const modelId = `fake-model-${suffix}`;
  execFileSync('uv', ['run', '--no-sync', 'python', '-c',
    "import os; from packages.vault import Vault; Vault().set(os.environ['FIXTURE_NAME'], os.environ['FIXTURE_VALUE'])"], {
    cwd: project, encoding: 'utf8', env: { ...process.env, SPATIAL_STATE_ROOT: resolve(project, configuredRoot),
      FIXTURE_NAME: credential, FIXTURE_VALUE: sentinel },
  });
  const registered = await request.post(`${api}/api/v1/models`, { data: {
    provider: 'e2e-fixture', modelId, defaultThinking: 'high', credentialName: credential,
  } });
  expect(registered.status()).toBe(201);
  expect((await request.put(`${api}/api/v1/models/default`, { data: { ref: `e2e-fixture/${modelId}` } })).ok()).toBe(true);
  const responses: Promise<string>[] = [];
  page.on('response', response => {
    if (new URL(response.url()).pathname.startsWith('/api/v1/')) responses.push(response.text().catch(() => ''));
  });
  await page.goto('/resources/gpu');
  const tabs = page.getByRole('navigation', { name: '资源标签' });
  const compute = tabs.locator('a[href="/resources/gpu"]');
  const nodeCount = (await (await request.get(`${api}/api/v1/nodes`)).json()).nodes.length;
  await expect(compute.locator('small')).toHaveText(`${nodeCount} 节点`);
  await testInfo.attach('resource-tab-count-readiness.json', { contentType: 'application/json', body: JSON.stringify({
    nodeCount, href: await compute.getAttribute('href'), snapshot: await tabs.ariaSnapshot(),
  }) });
  await expect(compute).toHaveAccessibleName(new RegExp(`^算力\\s*${nodeCount} 节点$`));
  await compute.focus();
  await expect(compute).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(tabs.getByRole('link', { name: /^模型/ })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/resources\/models$/);
  const row = page.getByTestId('llm-models-table').getByRole('row').filter({ hasText: modelId });
  await expect(row).toContainText(`vault:${credential}`);
  await expect(row.getByLabel('凭据已配置，值已隐藏')).toHaveText('••••••••');
  await expect(row).toContainText('默认');
  await expect(row).toContainText('未测量');
  await expect(row.getByRole('cell', { name: '* / * / *', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: '机器人模型目录' })).toContainText('尚未接入');
  await page.getByRole('button', { name: '编辑价格表', exact: true }).click();
  await page.getByLabel(`${modelId} 输入 ($/MTok)`, { exact: true }).fill('0');
  await page.getByLabel(`${modelId} 输出 ($/MTok)`, { exact: true }).fill('3.25');
  await page.getByRole('button', { name: '添加 GPU 费率' }).click();
  const nodes = page.getByLabel(/GPU \d+ 节点名称/);
  const gpuIndex = await nodes.count();
  await nodes.last().fill(`e2e-gpu-${suffix}`);
  await page.getByLabel(`GPU ${gpuIndex} 美元每小时`, { exact: true }).fill('.5');
  await page.getByRole('button', { name: '保存价格表', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('价格表已保存');
  await page.reload();
  await expect(row.getByRole('cell', { name: '$0.00 / $3.25 / *', exact: true })).toBeVisible();
  await expect(row.getByLabel('凭据已配置，值已隐藏')).toBeVisible();
  await page.getByText(/^价格修改记录/).click();
  await expect(page.getByText('user:local', { exact: true }).first()).toBeVisible();
  const saved = await (await request.get(`${api}/api/v1/prices`)).json();
  expect(saved.models.find((price: { modelId: string }) => price.modelId === modelId)).toEqual({
    provider: 'e2e-fixture', modelId, inputUsdPerMillion: 0, outputUsdPerMillion: 3.25, cacheReadUsdPerMillion: null,
  });
  expect(saved.audit.at(-1).actor).toEqual({ kind: 'user', name: 'local' });
  expect(saved.audit.at(-1).at).toBeTruthy();
  const controlBackground = () => page.getByRole('button', { name: '刷新', exact: true }).evaluate(
    element => getComputedStyle(element).backgroundColor,
  );
  await page.getByLabel('Color theme').selectOption('dark');
  await expect.poll(controlBackground).toBe('rgb(32, 31, 29)');
  await page.screenshot({ path: testInfo.outputPath('models-dark-1440.png'), fullPage: true });
  await page.getByLabel('Color theme').selectOption('light');
  await expect.poll(controlBackground).toBe('rgb(240, 239, 234)');
  await page.screenshot({ path: testInfo.outputPath('models-light-1440.png'), fullPage: true });
  await page.setViewportSize({ width: 400, height: 900 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
  await page.screenshot({ path: testInfo.outputPath('models-light-400.png'), fullPage: true });
  for (const body of await Promise.all(responses)) expect(body.includes(sentinel)).toBe(false);
  expect((await page.content()).includes(sentinel)).toBe(false);
});
