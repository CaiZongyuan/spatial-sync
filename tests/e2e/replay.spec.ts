import { expect, test } from '@playwright/test';
import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import type {} from '../../apps/web/src/components/Workbench';

const fixture = resolve('../../tests/fixtures/recordings/v1');
const expected = JSON.parse(readFileSync(join(fixture, 'expected.json'), 'utf8')) as {
  tubeKey: string; robotKey: string;
  samples: { timeMs: number; seq: number; tubePose: { p: number[]; q: number[] };
    tubeLocation: string; robotJoints: number[] }[];
};

test('plays the v1 Synthetic fixture with read-only Inspector and ten exact log comparisons', async ({ page, baseURL }) => {
  const liveRequests: string[] = [];
  page.on('request', (request) => { if (new URL(request.url()).pathname.startsWith('/api/')) liveRequests.push(request.url()); });
  page.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname.startsWith('/api/')) liveRequests.push(socket.url());
  });
  await page.route('**/fixture/v1/**', async (route) => {
    const relative = new URL(route.request().url()).pathname.split('/fixture/v1/')[1];
    const path = join(fixture, relative);
    if (!existsSync(path)) await route.fulfill({ status: 404 });
    else await route.fulfill({ path, contentType: relative.endsWith('.json') ? 'application/json' : 'application/octet-stream' });
  });
  const src = new URL('/fixture/v1/recording.json', baseURL).href;
  await page.goto(`/replay/v1?src=${encodeURIComponent(src)}`);
  await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
  await expect(page.getByText('Read-only replay · complete')).toBeVisible();
  await page.getByRole('button', { name: 'Play replay' }).click();
  await expect(page.getByRole('button', { name: 'Pause replay' })).toBeEnabled();
  await expect.poll(() => page.locator('[aria-label="Replay position"]').inputValue().then(Number)).toBeGreaterThan(100);
  await page.getByRole('button', { name: 'Pause replay' }).click();
  await expect(page.getByRole('button', { name: 'Play replay' })).toBeEnabled();
  const paused = await page.getByTestId('replay-time').textContent();
  await page.getByLabel('Playback speed').selectOption('4');
  await expect(page.getByTestId('replay-time')).toHaveText(paused!);

  for (const sample of expected.samples) {
    await page.locator('[aria-label="Replay position"]').evaluate((element, value) => {
      const input = element as HTMLInputElement;
      input.value = String(value > 0 ? value - 1 : 1);
    }, sample.timeMs);
    await page.getByLabel('Replay position').press(sample.timeMs > 0 ? 'ArrowRight' : 'ArrowLeft');
    await expect.poll(() => page.evaluate(() => window.__spatialDebug?.getSample()?.seq)).toBe(sample.seq);
    const actual = await page.evaluate(({ tubeKey, robotKey }) => ({
      pose: window.__spatialDebug?.getAuthoritativePose(tubeKey),
      location: window.__spatialDebug?.getEntityState(tubeKey)?.values.location,
      joints: window.__spatialDebug?.getEntityState(robotKey)?.values.jointPositions,
    }), expected);
    expect(actual).toEqual({ pose: sample.tubePose, location: sample.tubeLocation, joints: sample.robotJoints });
  }
  await page.getByTestId(`entity-${expected.robotKey}`).click();
  await page.getByTestId('action-moveJoints').locator('summary').click();
  await expect(page.getByRole('button', { name: 'Run moveJoints', exact: true })).toBeDisabled();
  await expect(page.getByLabel('moveJoints positions', { exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Close session', exact: true })).toBeDisabled();
  expect(liveRequests).toEqual([]);
  await page.screenshot({ path: '../../.scratch/info-i4-replay/fixture-replay.png' });
});
