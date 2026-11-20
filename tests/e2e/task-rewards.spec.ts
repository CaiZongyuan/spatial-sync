import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('reward slider saves an exact Task revision and queues preflight for the new Plan', async ({ page, request }) => {
  const root = resolve('../..'), python = process.env.SPATIAL_PYTHON ?? resolve(root, '.venv/bin/python');
  const archive = execFileSync(python, ['-m', 'tests.fixtures.reward_task'], { cwd: root });
  const uploaded = await request.post(`${api}/api/v1/tasks`, {
    headers: { 'Content-Type': 'application/x-tar' }, data: archive,
  });
  expect(uploaded.status(), await uploaded.text()).toBe(201);
  const source = await uploaded.json();
  const planDocument = JSON.parse(execFileSync(python, ['-c', [
    'import json, sys',
    'from tests.fixtures.training_task import training_plan',
    'print(json.dumps(training_plan(sys.argv[1], algorithm={"library":"rsl_rl","name":"PPO","version":"5.0.1"}, evaluation={"scene":"microduck-arena@1","episodes":30,"threshold":1})))',
  ].join('\n'), source.id], { cwd: root, encoding: 'utf8' }));
  const submitted = await request.post(`${api}/api/v1/plans`, { data: planDocument });
  expect(submitted.status(), await submitted.text()).toBe(201);
  const plan = await submitted.json();

  await page.goto(`/plans/${plan.id}`);
  await page.getByRole('button', { name: '调整奖励权重' }).click();
  await expect(page.locator(`code[title="${source.contentHash}"]`)).toBeVisible();
  const upright = page.getByRole('slider', { name: /upright/ });
  await expect(upright).toHaveValue('1');
  await upright.press('Home');
  await expect(upright).toHaveValue('-10');
  const revision = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api/v1/plans/${plan.id}/revisions`);
  await page.getByRole('button', { name: '保存为计划新修订' }).click();
  const revised = await revision;
  expect(revised.status(), await revised.text()).toBe(201);
  const current = await revised.json();
  expect(current.revision).toBe(2);
  expect(current.approvalId).not.toBe(plan.approvalId);
  expect(current.task).not.toBe(source.id);
  const [name, version] = current.task.slice(5).split('@');
  const task = await (await request.get(`${api}/api/v1/tasks/${name}/${version}`)).json();
  expect(task.task.declarative.rewards[0].weight).toBe(-10);
  expect(task.task.declarative.layerB).toEqual(source.task.declarative.layerB);
  await expect(page.getByRole('slider', { name: /upright/ })).toHaveValue('-10');
  await expect(page.locator(`code[title="${task.contentHash}"]`)).toBeVisible();

  const validation = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/v1/jobs');
  await page.getByRole('button', { name: '发起当前修订预检' }).click();
  const queued = await validation;
  expect(queued.status(), await queued.text()).toBe(202);
  const job = await queued.json();
  expect(job).toMatchObject({ kind: 'plan_validation', planRevision: 2, taskContentHash: task.contentHash });
  expect((await request.post(`${api}/api/v1/jobs`, { data: { planId: plan.id, planRevision: 2, seed: 42 } })).status())
    .toBe(409);
});
