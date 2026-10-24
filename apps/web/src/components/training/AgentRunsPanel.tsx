import { Link } from '@tanstack/react-router';
import { useContext, useEffect, useState } from 'react';
import { listJobs, type AgentRunJob, type ManagedJobStatus } from '../../lib/jobs';
import { agentMoney, agentRunStatus } from '../../lib/agent-runs';
import { ApprovalInboxContext } from '../../lib/approvals';
import type { AgentTrainingPlanDocument } from '../../lib/training';
import { ApprovalCard } from '../ApprovalsPage';
import './agent-runs.css';

export function AgentRunsPanel({ plans, query, status, onQuery, onStatus }: {
  plans?: AgentTrainingPlanDocument[]; query: string; status: string;
  onQuery: (value: string) => void; onStatus: (value: ManagedJobStatus | undefined) => void;
}) {
  const [jobs, setJobs] = useState<AgentRunJob[]>(), [error, setError] = useState<string>();
  const [retry, setRetry] = useState(0);
  const approvals = useContext(ApprovalInboxContext);
  useEffect(() => {
    let disposed = false, active: AbortController | undefined;
    const read = async () => {
      if (active) return;
      const controller = new AbortController(); active = controller;
      try { const result = await listJobs({ kind: 'agent-run' }, controller.signal); if (!disposed) { setJobs(result); setError(undefined); } }
      catch (failure) { if (!disposed) setError((failure as Error).message); }
      finally { active = undefined; }
    };
    void read(); const timer = setInterval(() => void read(), 3000);
    return () => { disposed = true; active?.abort(); clearInterval(timer); };
  }, [retry]);
  const filtered = jobs?.filter(job => (!status || job.status === status)
    && `${job.jobId} ${job.agent} ${job.model} ${job.planId}`.toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => b.createdAt - a.createdAt);
  const pending = approvals.inbox?.approvals.filter(record => record.status === 'pending'
    && record.details.plan?.kind === 'training' && record.details.plan.layer === 'agent');
  return <section className="resource-panel agent-runs-panel" data-testid="agent-runs-panel" aria-label="Agent 运行">
    <header><div><h2>Agent 运行</h2><span>固定 Harness 与公开 train 评估</span></div><button onClick={() => setRetry(value => value + 1)}>刷新 Agent 运行</button></header>
    <div className="resource-panel-body"><div className="training-filters"><label>搜索<input aria-label="搜索 Agent 运行" value={query} onChange={event => onQuery(event.target.value)} /></label>
      <label>状态<select aria-label="筛选 Agent 运行状态" value={status} onChange={event => onStatus(event.target.value as ManagedJobStatus || undefined)}><option value="">全部状态</option>
        {Object.entries(agentRunStatus).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
      {error && <p role="alert">Agent 运行暂不可用：{error}</p>}
      <div className="resource-table-scroll"><table aria-label="Agent 运行作业表"><thead><tr><th>作业 / Agent</th><th>模型 / 思考等级</th><th>状态</th><th>题次尝试</th><th>LLM 估算</th><th>固定计划</th></tr></thead><tbody>
        {filtered?.map(job => <tr key={job.jobId}><td><Link to="/train/jobs/$id" params={{ id: job.jobId }}>{job.jobId}</Link><small>{job.agent} · Harness v{job.harnessVersion}</small></td>
          <td>{job.model}<small>{job.thinking}</small></td><td>{agentRunStatus[job.status]}{job.reason && <small>{job.reason}</small>}</td><td>{job.attemptedCaseRuns} / {job.allocatedCaseRuns}</td>
          <td>{agentMoney(job.usage.llmUsd)}{job.usage.llmUsd == null && <small>已知 {agentMoney(job.usage.knownLlmUsd)} · 预留 {agentMoney(job.usage.reservedUsd)}</small>}</td>
          <td><Link to="/plans/$planId" params={{ planId: job.planId }}>{job.planId}</Link><small>r{job.planRevision}</small></td></tr>)}
      </tbody></table></div>{jobs && !filtered?.length && <p>当前筛选暂无 Agent 运行。从已批准的 Agent 计划启动。</p>}
      {!jobs && !error && <p role="status">正在读取 Agent 运行…</p>}
      <h3>Agent 计划与审批</h3>{pending?.map(record => <ApprovalCard key={record.approvalId} record={record}
        canDecide={approvals.inbox?.role === 'operator' && !approvals.error} />)}
      {plans?.map(plan => <p key={plan.id}><Link to="/plans/$planId" params={{ planId: plan.id }}>{plan.title}</Link> · r{plan.revision} · {plan.agent.id} / v{plan.agent.harnessVersion}</p>)}
      {!plans?.length && <p>暂无 Agent 计划。先提交固定 Agent 输入、范围与预算的计划。</p>}
    </div>
  </section>;
}
