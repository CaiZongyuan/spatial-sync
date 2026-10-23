import { expect, test } from '@playwright/test';
import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { promisify } from 'node:util';

const api = process.env.SPATIAL_API_URL ?? 'http://127.0.0.1:8000';

test('overbudget job approval shows measured estimate and remaining budget before releasing one fake lease', async ({ page, request }, info) => {
  const python = process.env.SPATIAL_PYTHON ?? resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python');
  const name = `budget-${randomUUID()}`;
  const { stdout } = await promisify(execFile)(python, [resolve('../../tests/fixtures/training_task.py'), name], { encoding: 'buffer' });
  const uploaded = await request.post(`${api}/api/v1/tasks`, { data: stdout, headers: { 'Content-Type': 'application/x-tar' } });
  expect(uploaded.ok(), await uploaded.text()).toBe(true);
  const task = await uploaded.json();
  const submitted = await request.post(`${api}/api/v1/plans`, { data: {
    kind: 'training', title: '预算审批边界', goal: '仅验证审批与租约，不执行训练', task: task.id,
    algorithm: { library: 'rsl_rl', name: 'PPO', version: '5.4.1' }, model: { actor: [32], critic: [32] },
    hyperparameters: { num_envs: 4, max_iterations: 10 }, jobs: { seeds: [42] }, budget: { gpuHours: 0.0001 },
    evaluation: { scene: 'robot-and-tubes@2', episodes: 1, threshold: 1 }, rationale: '受控数据验证超预算关口',
    hypotheses: [{ id: 'H1', cause: '测试预算门限', change: '受控预计时长', predict: { metric: 'success', to: '>= 1' } }],
    notebookRead: [], interview: '问题：执行训练吗？推荐：不执行。回答：是，仅检查审批。',
  } });
  expect(submitted.ok(), await submitted.text()).toBe(true);
  const plan = await submitted.json();
  expect((await request.post(`${api}/api/v1/approvals/${plan.approvalId}/decision`, { data: { decision: 'approve' } })).ok()).toBe(true);
  expect((await request.post(`${api}/e2e/training-node`, { data: { planId: plan.id } })).ok()).toBe(true);
  const started = await request.post(`${api}/api/v1/jobs`, { data: { planId: plan.id, seed: 42 } });
  expect(started.status()).toBe(202);
  const job = await started.json();
  expect(job).toMatchObject({ status: 'queued', code: 'pending_approval', leaseId: null, estimatedGpuHours: 0.1 });
  try {
    await page.goto('/approvals');
    const card = page.getByTestId(`approval-${job.approvalId}`);
    await expect(card).toContainText('超预算训练作业');
    await expect(card.locator('dl')).toContainText('预计 GPU 小时0.1');
    await expect(card.locator('dl')).toContainText('计划剩余 GPU 小时0.0001');
    expect((await (await request.get(`${api}/api/v1/jobs/${job.jobId}`)).json()).leaseId).toBeNull();
    await card.getByLabel('备注').fill('批准受控租约检查，测试不会启动训练器');
    await card.getByRole('button', { name: '批准', exact: true }).click();
    await expect(card).toContainText('已批准');
    const allocated = await (await request.get(`${api}/api/v1/jobs/${job.jobId}`)).json();
    expect(allocated.leaseId).toEqual(expect.any(String));
    expect(allocated.status).toBe('queued');
    await card.screenshot({ path: info.outputPath('job-budget-approval.png') });
  } finally {
    await request.post(`${api}/api/v1/jobs/${job.jobId}/cancel`);
    const current = await (await request.get(`${api}/api/v1/jobs/${job.jobId}`)).json();
    if (current.leaseId) {
      const released = await request.post(`${api}/e2e/training-node`, { data: { planId: plan.id, reports: [{
        jobId: job.jobId, leaseId: current.leaseId, status: 'cancelled', elapsedSeconds: 0,
      }] } });
      expect(released.ok(), await released.text()).toBe(true);
    } else if (job.approvalId) await request.post(`${api}/api/v1/approvals/${job.approvalId}/decision`, { data: { decision: 'deny' } });
  }
});
