import { request } from './api';
import type { AgentRunJob } from './jobs';
import type { AgentTrainingPlanContent } from './training';

export const startAgentRun = (plan: AgentTrainingPlanContent, requestId: string) =>
  request<AgentRunJob>(`/agents/${encodeURIComponent(plan.agent.id.replace(/^agent\//, ''))}/runs`, {
    method: 'POST', body: JSON.stringify({ suite: plan.suite, planId: plan.id, planRevision: plan.revision,
      version: plan.agent.harnessVersion, split: 'train', requestId }),
  });

export const agentRunStatus: Record<AgentRunJob['status'], string> = {
  admitting: '正在保存运行意图', queued: '排队中', running: '运行中', cancelling: '正在取消',
  succeeded: '已完成', failed: '失败', cancelled: '已取消',
};
export const agentMoney = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '未知' :
  value !== 0 && Math.abs(value) < .000001 ? `$${value.toExponential(2)}` :
    `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}`;
