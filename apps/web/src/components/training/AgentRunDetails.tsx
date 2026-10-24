import { Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { request } from '../../lib/api';
import { agentMoney, agentRunStatus } from '../../lib/agent-runs';
import { cancelJob, type AgentArtifact, type AgentRunJob } from '../../lib/jobs';
import { suiteSegment } from '../../lib/evaluations';
import { numberOrUnknown } from '../../lib/training-presentation';
import type { AgentTrainingPlanDocument, TrainingPlanDocument } from '../../lib/training';
import { TrainingPlanDetails } from '../TrainingPlanDetails';
import { LoopBudgetSummary } from './LoopBudgetSummary';
import type { LoopBudget } from '../../lib/notebooks';
import './agent-runs.css';

const artifactNames: Record<AgentArtifact['kind'], string> = {
  'platform-trace': '受信平台 Trace', 'sdk-messages': 'SDK 模型消息', 'final-state': '最终状态快照',
  score: '评分回执', usage: '受信用量',
};

export function AgentRunDetails({ job, error, refresh }: { job: AgentRunJob; error?: string; refresh: () => void }) {
  const [plan, setPlan] = useState<AgentTrainingPlanDocument>(), [planError, setPlanError] = useState<string>();
  const [pending, setPending] = useState(false), [actionError, setActionError] = useState<string>();
  const [goalBudget, setGoalBudget] = useState<LoopBudget>(), [goalError, setGoalError] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    void request<{ budget: LoopBudget }>(`/loops/${encodeURIComponent(job.goalId)}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) { setGoalBudget(value.budget); setGoalError(undefined); } })
      .catch(failure => { if (!controller.signal.aborted) setGoalError((failure as Error).message); });
    return () => controller.abort();
  }, [job.goalId, job.usage.knownLlmUsd, job.status]);
  useEffect(() => {
    const controller = new AbortController(); setPlan(undefined); setPlanError(undefined);
    void request<TrainingPlanDocument>(`/plans/${encodeURIComponent(job.planId)}?revision=${job.planRevision}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) {
        if (value.layer !== 'agent') throw new Error('固定计划不是 Agent 计划'); setPlan(value);
      } }).catch(failure => { if (!controller.signal.aborted) setPlanError((failure as Error).message); });
    return () => controller.abort();
  }, [job.planId, job.planRevision]);
  const act = async (cleanup: boolean) => {
    setPending(true); setActionError(undefined);
    try {
      if (cleanup) await request(`/evaluations/${encodeURIComponent(job.evaluationId)}/cleanup`, { method: 'POST' });
      else await cancelJob(job.jobId);
      refresh();
    } catch (failure) { setActionError((failure as Error).message); }
    finally { setPending(false); }
  };
  const valid = job.caseRuns.filter(caseRun => caseRun.status === 'valid').length;
  const invalid = job.caseRuns.filter(caseRun => !['valid', 'running', 'queued'].includes(caseRun.status)).length;
  return <main className="job-detail agent-run-detail" data-testid="agent-run-detail">
    <header className="page-header"><div><p className="eyebrow">AGENT RUN · TRAIN</p><h1>Agent 运行详情</h1>
      <p><code>{job.jobId}</code> · {agentRunStatus[job.status]}</p></div><Link to="/train">返回训练</Link></header>
    {error && <p role="alert">{error} <button onClick={refresh}>重试读取</button></p>}
    <div className="job-stat-strip" aria-label="Agent 运行指标">
      <div><span>已尝试 / 固定题次</span><strong>{job.attemptedCaseRuns} / {job.allocatedCaseRuns}</strong></div>
      <div><span>有效 / 无效或取消</span><strong>{valid} / {invalid}</strong></div>
      <div><span>Token / 请求</span><strong>{numberOrUnknown(job.usage.tokens, 0)} / {numberOrUnknown(job.usage.requests, 0)}</strong></div>
      <div><span>LLM 估算</span><strong>{agentMoney(job.usage.llmUsd)}</strong></div>
    </div>
    <div className="agent-run-links"><Link to="/eval/$suiteId" params={{ suiteId: suiteSegment(job.suite) }}>查看 Agent 计分板</Link>
      <Link to="/cost" search={{ range: '7d', plan: `${job.planId}@${job.planRevision}`, goal: job.goalId, agent: job.agent }}>查看本计划成本</Link>
      {['admitting', 'queued', 'running'].includes(job.status) && <button disabled={pending} onClick={() => void act(false)}>取消 Agent 运行</button>}
      {job.status === 'cancelling' && <span>正在取消，等待服务确认资源清理。</span>}
      {job.cleanupIncomplete && <><span role="alert">清理尚未确认，用量与资源承诺仍保留。</span><button disabled={pending} onClick={() => void act(true)}>重试清理</button></>}
    </div>{actionError && <p role="alert">{actionError}</p>}
    {job.goalException && <p className="loop-budget-warning" role="status">{job.goalException.dimensions.includes('days') ? 'Goal 期限已过；' : 'Goal 预算已用尽；'}本计划获有限例外，仍按本计划剩余执行额度运行。原目标上限与截止时间保持不变。</p>}
    <section className="panel"><h2>固定运行身份与用量</h2><dl className="training-plan-facts">
      <div><dt>Agent / Harness</dt><dd>{job.agent} · v{job.harnessVersion}</dd></div>
      <div><dt>模型 / 思考等级</dt><dd>{job.model} · {job.thinking}</dd></div>
      <div><dt>计划 / Goal</dt><dd><Link to="/plans/$planId" params={{ planId: job.planId }}>{job.planId}</Link> · r{job.planRevision} · {job.goalId}</dd></div>
      <div><dt>执行时间 / 本次额度</dt><dd>{numberOrUnknown(job.executionSeconds, 2)} / {job.allocatedWallSeconds}s · 清理 {numberOrUnknown(job.cleanupSeconds, 2)}s</dd></div>
      <div><dt>已知部分 / 未确认预留</dt><dd>{agentMoney(job.usage.knownLlmUsd)} / {agentMoney(job.usage.reservedUsd)} · {job.usage.knownTokens.toLocaleString()} / {job.usage.reservedTokens.toLocaleString()} Token</dd></div>
      <div><dt>未确认用量请求</dt><dd>{job.usage.unknownUsageRequests} · 价格版本 {job.usage.priceRevisions?.join(', ') || '尚无请求'}</dd></div>
      <div><dt>实际执行资源</dt><dd>本地受管执行 · CPU · {job.gpuHours} GPU 小时</dd></div>
      {job.reason && <div><dt>状态原因</dt><dd>{job.reason}</dd></div>}
    </dl><p className="resource-note">有效负例保留得分；无效或不完整题次不补成零分。reported 记录不代表已接受或部署。</p></section>
    {goalError && <p role="alert">目标预算暂不可用：{goalError}</p>}{goalBudget && <LoopBudgetSummary budget={goalBudget} />}
    <section className="panel"><h2>逐题运行记录</h2><div className="resource-table-scroll"><table aria-label="Agent 题次结果"><thead><tr><th>题目 / 重复</th><th>状态</th><th>得分 / 100</th><th>维度得分</th><th>证据</th></tr></thead><tbody>
      {job.caseRuns.map(caseRun => <tr key={caseRun.caseRunId}><td>{caseRun.caseId} · #{caseRun.repetition}</td><td>{caseRun.status}{caseRun.error && <small>{caseRun.error.code}</small>}</td>
        <td>{numberOrUnknown(caseRun.score, 2)}</td><td>{caseRun.dimensions ? Object.entries(caseRun.dimensions).map(([key, score]) => `${key}: ${score}`).join(' · ') : '未形成有效评分'}</td>
        <td>{caseRun.recordingId && <Link to="/replay/$recordingId" params={{ recordingId: caseRun.recordingId }}>查看回放</Link>}
          {job.artifacts.filter(artifact => artifact.caseRunId === caseRun.caseRunId).map(artifact => <a key={artifact.id} href={artifact.url} target="_blank" rel="noreferrer">{artifactNames[artifact.kind]}</a>)}</td></tr>)}
    </tbody></table></div>{!job.caseRuns.length && <p>等待已持久化的逐题结果。</p>}</section>
    <section className="panel"><h2>公开运行证据</h2><details><summary>查看全部公开证据（{job.artifacts.length} 项）</summary><ul>{job.artifacts.map(artifact => <li key={artifact.id}>
      <a href={artifact.url} target="_blank" rel="noreferrer">{artifactNames[artifact.kind]} · {artifact.caseRunId ?? '整次运行'}</a>
      <small>{artifact.source} · {artifact.size.toLocaleString()} B · {artifact.sha256.slice(0, 12)}</small></li>)}</ul></details>
      {!job.artifacts.length && <p>暂无已发布的公开证据。</p>}</section>
    {planError && <p role="alert">固定计划读取失败：{planError}</p>}
    {plan && <section className="panel"><h2>批准的 Agent 计划 · r{job.planRevision}</h2>
      {plan.loopBudget && <details><summary>提交审批时的目标预算快照</summary><LoopBudgetSummary budget={plan.loopBudget} /></details>}
      <TrainingPlanDetails plan={plan} changes={plan.changes} frozenInputs={plan.frozenInputs} /></section>}
  </main>;
}
