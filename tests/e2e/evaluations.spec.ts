import { expect, test } from '@playwright/test';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('leaving before a queued launch response releases only the session that its ticket started', async ({ page }) => {
  let release!: () => void;
  let received!: () => void;
  const heldResponse = new Promise<void>(resolve => { release = resolve; });
  const requestReceived = new Promise<void>(resolve => { received = resolve; });
  const writes: string[] = [];
  const ticket = { requestId: 'request-late-response', status: 'queued', sceneVersion: 'lab-demo@3', source: 'synthetic', expiresAt: Date.now() / 1000 + 600 };
  await page.route('**/api/v1/sessions', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    received(); await heldResponse;
    await route.fulfill({ status: 202, json: ticket });
  });
  await page.route('**/api/v1/session-requests/request-late-response', async route => {
    expect(route.request().method()).toBe('DELETE');
    writes.push(new URL(route.request().url()).pathname);
    await route.fulfill({ json: { ...ticket, status: 'started', sessionId: 'session-owned-late-ticket' } });
  });
  await page.route('**/api/v1/sessions/*', async route => {
    if (route.request().method() !== 'DELETE') return route.continue();
    writes.push(new URL(route.request().url()).pathname);
    await route.fulfill({ status: 204 });
  });
  try {
    await page.goto('/scenes/lab-demo/3');
    await page.getByRole('button', { name: '启动会话' }).click();
    await requestReceived;
    await page.getByRole('link', { name: '← 场景库', exact: true }).click();
    await expect(page).toHaveURL(/\/scenes$/);
    release();
    await expect.poll(() => writes).toEqual([
      '/api/v1/session-requests/request-late-response',
      '/api/v1/sessions/session-owned-late-ticket',
    ]);
  } finally { release(); }
});
