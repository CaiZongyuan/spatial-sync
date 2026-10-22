import { expect, test, type Page } from '@playwright/test';

declare global {
  interface Window {
    resourceReads: {
      mode: 'ready' | 'defer' | 'fail';
      ignoreAbort: boolean;
      pending: number;
      aborted: number;
      writes: number[];
      release(): Promise<void>;
    };
  }
}

async function installResourceResponses(page: Page) {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    const provider = 'read-fixture', modelId = 'price-race';
    let prices = { version: 1, revision: 0, canEdit: true,
      models: [{ provider, modelId, inputUsdPerMillion: 1, outputUsdPerMillion: null, cacheReadUsdPerMillion: null }],
      gpus: [], audit: [] };
    const gates: (() => void)[] = [];
    const harness = window.resourceReads = {
      mode: 'ready', ignoreAbort: false, pending: 0, aborted: 0, writes: [] as number[],
      async release() {
        for (const release of gates.splice(0)) release();
        // Let the actual Promise.all and React commit complete after the old replies.
        await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
      },
    };
    const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
      status, headers: { 'Content-Type': 'application/json' },
    });
    window.fetch = (input, options) => {
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      const path = url.pathname;
      if (!['/api/v1/models', '/api/v1/vault', '/api/v1/prices'].includes(path)) return original(input, options);
      if (options?.method === 'PUT') {
        const incoming = JSON.parse(String(options.body));
        harness.writes.push(incoming.revision);
        if (incoming.revision !== prices.revision) return Promise.resolve(response({ detail: { code: 'prices_changed' } }, 409));
        prices = { ...incoming, version: 1, canEdit: true, revision: prices.revision + 1,
          audit: [...prices.audit, { revision: prices.revision + 1, at: '2026-10-23T00:00:00Z', actor: { kind: 'user', name: 'local' } }] };
        return Promise.resolve(response(prices));
      }
      const snapshot = structuredClone(path.endsWith('/prices') ? prices : path.endsWith('/vault') ? { names: [] }
        : { models: [{ provider, modelId, ref: `${provider}/${modelId}`, defaultThinking: 'high', credentialName: null,
          isDefault: false, health: 'unknown', latencyP50Ms: null, users: null }] });
      if (harness.mode === 'fail' && path.endsWith('/models'))
        return Promise.resolve(response({ detail: { code: 'model_read_failed' } }, 503));
      if (harness.mode === 'ready') return Promise.resolve(response(snapshot));
      harness.pending++;
      return new Promise<Response>((resolve, reject) => {
        let settled = false;
        const signal = options?.signal;
        signal?.addEventListener('abort', () => {
          harness.aborted++;
          if (!harness.ignoreAbort && !settled) { settled = true; harness.pending--; reject(signal.reason); }
        }, { once: true });
        gates.push(() => {
          if (!settled) { settled = true; harness.pending--; resolve(response(snapshot)); }
        });
      });
    };
  });
}

test('a deferred old refresh cannot roll back a successful price save or the next edit', async ({ page }) => {
  await installResourceResponses(page);
  await page.goto('/resources/models');
  const row = page.getByTestId('llm-models-table').getByRole('row').filter({ hasText: 'price-race' });
  await expect(row).toContainText('$1.00 / * / *');
  await page.evaluate(() => { window.resourceReads.mode = 'defer'; window.resourceReads.ignoreAbort = true; });
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.resourceReads.pending)).toBe(3);
  await page.getByRole('button', { name: '编辑价格表', exact: true }).click();
  await page.getByLabel('price-race 输入 ($/MTok)', { exact: true }).fill('2.75');
  await page.getByRole('button', { name: '保存价格表', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('价格表已保存');
  await expect(row).toContainText('$2.75 / * / *');
  // The fake transport deliberately completes despite abort, so abort alone cannot pass.
  await page.evaluate(() => window.resourceReads.release());
  await expect(row).toContainText('$2.75 / * / *');
  await expect(page.getByRole('status')).toContainText('价格表已保存');
  await page.getByText(/^价格修改记录/).click();
  await expect(page.getByText('修订 1', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '编辑价格表', exact: true }).click();
  await expect(page.getByLabel('price-race 输入 ($/MTok)', { exact: true })).toHaveValue('2.75');
  await page.getByLabel('price-race 输入 ($/MTok)', { exact: true }).fill('3.5');
  await page.getByRole('button', { name: '保存价格表', exact: true }).click();
  await expect(row).toContainText('$3.50 / * / *');
  await expect(page.getByText(/^价格修改记录/)).toContainText('2');
  expect(await page.evaluate(() => window.resourceReads.writes)).toEqual([0, 1]);
  expect(await page.evaluate(() => window.resourceReads.pending)).toBe(0);
});

test('a failed read cancels its peers and leaving the page cancels a later batch', async ({ page }) => {
  await installResourceResponses(page);
  await page.goto('/resources/models');
  await expect(page.getByTestId('llm-models-table')).toContainText('price-race');
  await page.evaluate(() => { window.resourceReads.mode = 'fail'; });
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('model_read_failed');
  await expect.poll(() => page.evaluate(() => window.resourceReads.pending)).toBe(0);
  expect(await page.evaluate(() => window.resourceReads.aborted)).toBe(2);
  await page.evaluate(() => { window.resourceReads.mode = 'defer'; });
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.resourceReads.pending)).toBe(3);
  await page.getByRole('navigation', { name: '资源标签' }).getByRole('link', { name: '算力', exact: true }).click();
  await expect(page).toHaveURL(/\/resources\/gpu$/);
  await expect.poll(() => page.evaluate(() => window.resourceReads.pending)).toBe(0);
  expect(await page.evaluate(() => window.resourceReads.aborted)).toBe(5);
});

test('a read deadline reports a timeout and cancels every pending peer', async ({ page }) => {
  await installResourceResponses(page);
  await page.goto('/resources/models');
  await expect(page.getByTestId('llm-models-table')).toContainText('price-race');
  await page.clock.install();
  await page.evaluate(() => { window.resourceReads.mode = 'defer'; });
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.resourceReads.pending)).toBe(3);
  await page.clock.fastForward(8100);
  await expect(page.getByRole('alert')).toContainText('读取模型与价格超时');
  expect(await page.evaluate(() => window.resourceReads.pending)).toBe(0);
  expect(await page.evaluate(() => window.resourceReads.aborted)).toBe(3);
});
