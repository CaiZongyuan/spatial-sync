import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('robot archive drop opens the exact immutable visual and its pinned P8 report', async ({ page, request }) => {
  const root = resolve('../..'), python = process.env.SPATIAL_PYTHON ?? resolve(root, '.venv/bin/python');
  const bytes = execFileSync(python, ['-m', 'tests.fixtures.robot_archive'], { cwd: root });
  await page.goto('/assets');
  const drop = page.getByRole('group', { name: '上传机器人归档' });
  await expect(drop).toBeVisible();
  const transfer = await page.evaluateHandle(bytes => {
    const files = new DataTransfer();
    files.items.add(new File([new Uint8Array(bytes)], 'robot.tar', { type: 'application/x-tar' }));
    return files;
  }, [...bytes]);
  const result = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/v1/library/imports');
  await drop.dispatchEvent('drop', { dataTransfer: transfer });
  const uploaded = await result;
  expect(uploaded.ok(), await uploaded.text()).toBe(true);
  const receipt = await uploaded.json();
  expect(receipt.assetVersion).toMatch(/^robot\/browser-archive-probe@[1-9][0-9]*$/);
  const rev = receipt.assetVersion.split('@')[1];
  await expect(page).toHaveURL(new RegExp('/assets/robot/browser-archive-probe/' + rev + '$'));
  const detail = await (await request.get(`${api}/api/v1/library/assets/robot/browser-archive-probe/${rev}`)).json();
  expect(detail.id).toBe(receipt.assetVersion);
  expect(detail.contentHash).toBe(receipt.contentHash);
  expect(detail.files['joint-calibration.json']).toBeTruthy();
  await expect(page.getByLabel('素材静态模型预览')).toHaveAttribute('data-model-state', 'ready');
  const p8 = page.getByRole('region', { name: '双侧重力静置 P8' });
  await expect(p8).toBeVisible();
  const comparison = detail.validation.checks.find((check: { id: string }) => check.id === 'P8');
  if (comparison?.measurements) {
    const { newton, mujoco } = comparison.measurements;
    const table = p8.getByRole('table');
    await expect(table.getByRole('columnheader', { name: 'Newton' })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'MuJoCo' })).toBeVisible();
    const height = table.getByRole('row', { name: /基座高度/ });
    await expect(height.getByRole('cell').nth(0)).toHaveText(String(newton.baseHeightM));
    await expect(height.getByRole('cell').nth(1)).toHaveText(String(mujoco.baseHeightM));
  } else {
    expect(detail.validation.physics.status).toBe('skipped');
    await expect(p8).toContainText('物理检查已跳过');
    await expect(p8.getByRole('table')).toHaveCount(0);
  }
});
