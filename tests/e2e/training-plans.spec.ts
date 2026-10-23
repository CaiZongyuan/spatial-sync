import { expect, test } from '@playwright/test';
import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { promisify } from 'node:util';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('Task snapshots and training approvals show measured hypotheses, interview and prior changes without a session', async ({ page, request }, info) => {
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const name = `training-${randomUUID()}`;
  const { stdout } = await promisify(execFile)(python, [resolve('../../tests/fixtures/training_task.py'), name], { encoding: 'buffer' });
  const uploaded = await request.post(`${api}/api/v1/tasks`, { headers: { 'Content-Type': 'application/x-tar' }, data: stdout });
  expect(uploaded.ok(), await uploaded.text()).toBe(true);
  const task = await uploaded.json();
  const again = await (await request.post(`${api}/api/v1/tasks`, { headers: { 'Content-Type': 'application/x-tar' }, data: stdout })).json();
  expect(again).toEqual({ ...task, idempotent: true });
  const document = {
    kind: 'training', title: 'FR3 插入引导 · 第一轮', goal: '平台评估至少 85/100', task: task.id,
    algorithm: { library: 'rsl_rl', name: 'PPO', version: '5.4.1' }, model: { actor: [256, 128], critic: [256, 128] },
    hyperparameters: { num_envs: 4, max_iterations: 10, learning_rate: 0.001 }, jobs: { seeds: [42, 43] },
    budget: { gpuHours: 1 }, evaluation: { scene: 'robot-and-tubes@2', episodes: 100, threshold: 85 },
    rationale: '保持算法与模型，检验插入阶段的引导权重。',
    hypotheses: [{ id: 'H1.1', cause: '插入阶段引导过弱', change: 'held_goal 从 0.1 提高到 0.2',
      predict: { metric: 'stage.insert', from: 0.61, to: '>= 0.80' } }], notebookRead: [],
    interview: '| 问题 | Agent 推荐 | 人的回答 |\n| --- | --- | --- |\n| 这一轮检验什么？ | 只改变插入引导，目标 >= 0.80。 | 是，保持算法和模型。 |',
  };
  const created = await request.post(`${api}/api/v1/plans`, { headers: { 'X-Actor': 'agent:training-e2e' }, data: document });
  expect(created.ok(), await created.text()).toBe(true);
  const first = await created.json();
  const approvals = [first.approvalId];
  const liveRequests: string[] = [], sockets: string[] = [], errors: string[] = [];
  page.on('request', response => { if (new URL(response.url()).pathname.startsWith('/api/v1/sessions')) liveRequests.push(response.url()); });
  page.on('websocket', socket => sockets.push(socket.url()));
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto('/approvals');
    const card = page.getByTestId(`approval-${first.approvalId}`);
    await expect(card).toContainText('训练计划审批');
    await expect(card.getByRole('region', { name: '训练计划内容' })).toContainText(task.id);
    await expect(card).toContainText('H1.1 · 插入阶段引导过弱');
    await expect(card).toContainText('stage.insert');
    await expect(card.getByRole('table', { name: '训练访谈问答' })).toContainText('是，保持算法和模型。');
    await card.getByLabel('备注').fill('本轮作为对照，下一轮再调整预算');
    await card.getByRole('button', { name: '批准', exact: true }).click();
    await expect(card).toContainText('已批准');
    const secondResponse = await request.post(`${api}/api/v1/plans`, { data: {
      ...document, title: 'FR3 插入引导 · 第二轮', previous: first.id, budget: { gpuHours: 0.5 },
      interview: '| 问题 | Agent 推荐 | 人的回答 |\n| --- | --- | --- |\n| 预算是否减半？ | 0.5 小时。 | 是。 |',
    } });
    expect(secondResponse.ok(), await secondResponse.text()).toBe(true);
    const second = await secondResponse.json(); approvals.push(second.approvalId);
    const secondCard = page.getByTestId(`approval-${second.approvalId}`);
    await expect(secondCard).toContainText(first.id);
    const budget = secondCard.getByRole('table', { name: '训练计划变化' }).getByRole('row').filter({ hasText: 'budget' });
    await expect(budget).toContainText('"gpuHours": 1');
    await expect(budget).toContainText('"gpuHours": 0.5');
    await expect(secondCard).toContainText('预算是否减半');
    await secondCard.screenshot({ path: info.outputPath('training-approval-card.png') });
    await page.screenshot({ path: info.outputPath('training-approval.png'), fullPage: true });
    await secondCard.getByLabel('备注').fill('补充对照结果后再提交');
    await secondCard.getByRole('button', { name: '驳回', exact: true }).click();
    await expect(secondCard).toContainText('已驳回');
    await page.goto(`/plans/${second.id}`);
    await expect(page.getByRole('region', { name: '训练计划详情' })).toContainText('已驳回');
    await expect(page.getByTestId('training-plan-details')).toContainText('预算是否减半');
    await expect(page.locator('canvas')).toHaveCount(0);
    expect(liveRequests).toEqual([]); expect(sockets).toEqual([]); expect(errors).toEqual([]);
  } finally {
    for (const approval of approvals) {
      const current = await (await request.get(`${api}/api/v1/approvals/${approval}`)).json();
      if (current.status === 'pending') expect((await request.post(`${api}/api/v1/approvals/${approval}/decision`, { data: { decision: 'deny' } })).ok()).toBe(true);
    }
  }
});
