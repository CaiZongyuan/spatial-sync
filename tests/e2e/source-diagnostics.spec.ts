import { expect, test, type Page } from '@playwright/test';
import type {} from '../../apps/web/src/components/Workbench';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';
const value = (page: Page, label: string) =>
  page.getByText(label, { exact: true }).locator('..').locator('dd');

test('synthetic diagnostics keep unmeasured shape count and Runner heartbeat unknown', async ({ page, request }) => {
  const created = await request.post(`${api}/api/v1/sessions`, { data: { sceneId: 'lab-demo' } });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  try {
    await page.goto(`/sessions/${sessionId}`);
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    await page.getByRole('button', { name: 'Diagnostics' }).click();
    await expect(value(page, 'Shapes')).toHaveText('—');
    await expect(value(page, 'Runner heartbeat')).toHaveText('—');
    await expect(value(page, 'Gateway heartbeat')).toHaveText(/\d+ ms ago/);
  } finally {
    await request.delete(`${api}/api/v1/sessions/${sessionId}`);
  }
});

test('real Newton diagnostics show measured shapes, full readback and live paused heartbeat', async ({ page, request }, info) => {
  test.skip(process.env.SPATIAL_TEST_NEWTON !== '1', 'Requires the optional Newton environment and an owned API');
  const created = await request.post(`${api}/api/v1/sessions`, {
    data: { sceneId: 'falling-boxes', source: 'newton' }, timeout: 180_000,
  });
  expect(created.ok()).toBe(true);
  const { sessionId } = await created.json();
  const route = `${api}/api/v1/sessions/${sessionId}`;
  try {
    const manifest = await (await request.get(`${route}/scene`)).json();
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`/sessions/${sessionId}`);
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    await page.getByRole('button', { name: 'Diagnostics' }).click();
    await expect(value(page, 'Shapes')).toHaveText(String(manifest.source.shapeCount));
    const stats = await (await request.get(`${route}/stats`)).json();
    expect(stats.readbackBodyCount).toBe(stats.modelBodyCount);
    await expect(value(page, 'Readback bodies')).toHaveText(String(stats.modelBodyCount));
    await expect(value(page, 'Readback bytes')).toHaveText(`${stats.modelBodyCount * 28} B`);
    await expect(value(page, 'Runner heartbeat')).toHaveText(/^\d+ ms ago$/);
    await expect(value(page, 'Gateway heartbeat')).toHaveText(/^\d+ ms ago$/);
    await page.screenshot({ path: info.outputPath('newton-full-diagnostics.png'), fullPage: true });
    await page.getByRole('button', { name: 'Pause simulation' }).click();
    await expect(page.getByTestId('session-state')).toHaveText('已暂停');
    const paused = await (await request.get(`${route}/stats`)).json();
    await expect.poll(async () => (await (await request.get(`${route}/stats`)).json()).runnerHeartbeatSeq)
      .toBeGreaterThan(paused.runnerHeartbeatSeq);
    expect((await (await request.get(`${route}/stats`)).json()).seq).toBe(paused.seq);
    await expect(value(page, 'Runner heartbeat')).toHaveText(/^\d+ ms ago$/);
    await page.screenshot({ path: info.outputPath('newton-paused-diagnostics.png'), fullPage: true });
    process.kill(paused.runnerPID); // This test created and owns the source session.
    await expect(page.getByTestId('session-state')).toHaveText('失败');
    await expect(value(page, 'Runner heartbeat')).toHaveText(/^Unavailable · \d+ ms ago$/);
    await expect(value(page, 'Gateway heartbeat')).toHaveText(/^\d+ ms ago$/);
    await page.screenshot({ path: info.outputPath('newton-failed-diagnostics.png'), fullPage: true });
    expect(errors).toEqual([]);
    await info.attach('actual-source-diagnostics.json', {
      body: JSON.stringify({ manifestShapes: manifest.source.shapeCount, paused,
        failed: await (await request.get(`${route}/stats`)).json() }, null, 2),
      contentType: 'application/json',
    });
  } finally {
    await request.delete(route);
  }
});
