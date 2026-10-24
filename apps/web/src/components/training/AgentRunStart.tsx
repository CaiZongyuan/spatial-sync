import { Link, useNavigate } from '@tanstack/react-router';
import { useContext, useState } from 'react';
import { ApprovalInboxContext } from '../../lib/approvals';
import { startAgentRun } from '../../lib/agent-runs';
import type { AgentTrainingPlanDocument } from '../../lib/training';

export function AgentRunStart({ plan }: { plan: AgentTrainingPlanDocument }) {
  const [requestId] = useState(() => crypto.randomUUID());
  const [pending, setPending] = useState(false), [error, setError] = useState<string>();
  const approvals = useContext(ApprovalInboxContext), navigate = useNavigate();
  const start = async () => {
    setPending(true); setError(undefined);
    try {
      const job = await startAgentRun(plan, requestId);
      await navigate({ to: '/train/jobs/$id', params: { id: job.jobId } });
    } catch (failure) { setError((failure as Error).message); }
    finally { setPending(false); }
  };
  return <section className="panel agent-run-start" aria-label="启动 Agent 运行">
    <h3>运行已批准的 Agent 计划</h3>
    <p>{plan.agent.id} · Harness v{plan.agent.harnessVersion} · {plan.suite} · 固定修订 r{plan.revision}</p>
    <button type="button" onClick={() => void start()} disabled={pending || plan.status !== 'approved'
      || approvals.inbox?.role !== 'operator' || Boolean(approvals.error)}>
      {pending ? '正在接纳运行…' : '启动 Agent 运行'}</button>
    {plan.status !== 'approved' && <p>此修订须先通过人工审批。<Link to="/approvals">查看待审批</Link></p>}
    <p className="resource-note">按固定模型、范围与额度运行。重试保留同一请求标识；启动不授予 Target 人工审批权。</p>
    {error && <p role="alert">{error} · 可在核对计划后重试本次请求。</p>}
  </section>;
}
