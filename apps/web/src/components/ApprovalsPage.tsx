import { Link } from '@tanstack/react-router';
import { useContext, useState } from 'react';
import { ApprovalInboxContext, decideApproval, type ApprovalDecision, type ApprovalRecord } from '../lib/approvals';
import '../approvals.css';
import { LibraryDeletionApproval } from './LibraryDeletion';
import type { DeletionDetails } from '../lib/library-deletions';
import type { TrainingPlanDocument } from '../lib/training';
import { TrainingPlanDetails } from './TrainingPlanDetails';

const statusText: Record<ApprovalRecord['status'], string> = {
  pending: '待审批', approved: '已批准', denied: '已驳回', revoked: '已撤销', expired: '已超时', unavailable: '已关闭',
};
const subjectText: Record<ApprovalRecord['subject'], string> = {
  invocation: '高风险调用', plan: '计划', policy: '策略提升', job: '作业', deletion: '删除版本',
};
const decisionText: Record<ApprovalDecision, string> = { approve: '批准', deny: '驳回', revoke: '撤销' };
const actor = (value: ApprovalRecord['requestedBy']) => `${value.kind}:${value.name}`;
const time = (value: number) => new Date(value).toLocaleString('zh-CN', { hour12: false });

function ApprovalCard({ record, canDecide }: { record: ApprovalRecord; canDecide: boolean }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const processed = record.status !== 'pending';
  const last = record.decisions.at(-1);
  const training = record.details.plan?.kind === 'training';
  async function decide(decision: ApprovalDecision) {
    setBusy(true); setError(undefined);
    try { await decideApproval(record.approvalId, decision, note); setNote(''); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  return <article className="approval-card" data-testid={`approval-${record.approvalId}`} aria-label={`${subjectText[record.subject]}：${record.title}`}>
    <div className="approval-card-heading"><span className="eyebrow">{training ? '训练计划审批' : subjectText[record.subject]}</span>
      <span className={`approval-status is-${record.status}`}><i />{statusText[record.status]}</span></div>
    <h3>{record.title}</h3>
    {record.details.entityKey && <p className="approval-entity"><code>{record.details.entityKey}</code></p>}
    <dl className="approval-facts"><div><dt>提交者</dt><dd>{actor(record.requestedBy)}</dd></div>
      <div><dt>提交时间</dt><dd>{time(record.createdAtMs)}</dd></div>
      <div><dt>审批对象</dt><dd><code>{record.subjectId}</code></dd></div>
      {record.sessionId && <div><dt>会话</dt><dd><Link to="/sessions/$sessionId" params={{ sessionId: record.sessionId }}>{record.sessionId}</Link></dd></div>}
      {record.subject === 'job' && <><div><dt>训练计划</dt><dd><Link to="/plans/$planId" params={{ planId: record.details.planId! }}>{record.details.planId}</Link> · r{record.details.planRevision}</dd></div>
        <div><dt>预计 GPU 小时</dt><dd>{record.details.estimatedGpuHours}</dd></div>
        <div><dt>计划剩余 GPU 小时</dt><dd>{record.details.remainingGpuHours}</dd></div></>}
    </dl>
    {record.details.params && Object.keys(record.details.params).length > 0 && <details className="approval-params"><summary>调用参数</summary><pre>{JSON.stringify(record.details.params, null, 2)}</pre></details>}
    {training ? <TrainingPlanDetails plan={record.details.plan as unknown as TrainingPlanDocument} changes={record.details.changes} /> :
      record.details.plan && <details className="approval-params" open><summary>计划修订 {record.details.revision} · 步骤与预期结果</summary><pre>{JSON.stringify(record.details.plan, null, 2)}</pre></details>}
    {record.subject === 'deletion' && record.details.inspection && <LibraryDeletionApproval details={record.details as DeletionDetails} />}
    {last && <p className="approval-receipt">{actor(last.actor)} · {time(last.atMs)} · {decisionText[last.decision]}{last.note && <span>{last.note}</span>}</p>}
    {(!processed || record.canRevoke) && <label className="approval-note">备注
      <textarea rows={2} maxLength={4000} value={note} onChange={event => setNote(event.target.value)}
        placeholder={processed ? '撤销的理由（可选）' : '批准或驳回的理由（可选）'} disabled={busy || !canDecide} />
    </label>}
    <div className="approval-actions">
      {!processed ? <><button disabled={busy || !canDecide} onClick={() => void decide('deny')}>驳回</button>
        <button className="primary" disabled={busy || !canDecide} onClick={() => void decide('approve')}>批准</button></> :
        record.canRevoke ? <button disabled={busy || !canDecide} onClick={() => void decide('revoke')}>撤销</button> :
          <span className="approval-execution">{record.invocation?.startedSimTime != null ? '执行已开始' : record.status === 'revoked' ? '决定已撤销' : '审批已结束'}</span>}
    </div>
    {error && <p className="approval-error" role="alert">{error}</p>}
  </article>;
}

export function ApprovalsPage() {
  const { inbox, error } = useContext(ApprovalInboxContext);
  const canDecide = inbox?.role === 'operator' && !error;
  const pending = inbox?.approvals.filter(record => record.status === 'pending') ?? [];
  const processed = inbox?.approvals.filter(record => record.status !== 'pending') ?? [];
  const events = inbox?.approvals.flatMap(record => [
    { id: `${record.approvalId}-request`, at: record.createdAtMs, who: actor(record.requestedBy), text: `提交 ${record.title}` },
    ...record.decisions.map((decision, index) => ({ id: `${record.approvalId}-${index}`, at: decision.atMs,
      who: actor(decision.actor), text: `${decisionText[decision.decision]} ${record.title}`, note: decision.note })),
  ]).sort((a, b) => b.at - a.at).slice(0, 30) ?? [];
  return <main className="approvals-page" data-testid="approvals-page">
    <header className="approvals-page-heading"><h1>待审批</h1>
      <p>提交请求，由人决定 · 批准后才会执行对应操作</p></header>
    <div className="approval-stage-strip" role="group" aria-label="具身层阶段">
      {['素材', '场景', '计划', '作业', '评估', '策略'].map((label, index) => <button className="approval-stage"
        key={label} disabled aria-label={`${label}阶段概览暂不可用`}>
        <span className="approval-stage-label">0{index + 1} · {label}</span>
        <strong className="approval-stage-value">—</strong>
        <span className="approval-stage-state"><i aria-hidden="true" />阶段概览暂不可用</span>
        {index < 5 && <span className="approval-stage-arrow" aria-hidden="true">›</span>}
      </button>)}
    </div>
    {error && <p role="alert" className="approval-error">审批数据暂时不可用：{error}。正在自动重试。</p>}
    {inbox?.role === 'viewer' && <p className="approval-permission" role="status">只读访问 · 需要操作员权限才能处理审批。</p>}
    {!inbox && !error ? <p role="status">正在读取审批…</p> : inbox && <div className="approval-layout">
      <div className="approval-inboxes"><section className="approval-panel" aria-label="待审批收件箱"><header><h2>收件箱</h2><span className="approval-count">{inbox.pendingCount}</span></header>
        {pending.length ? pending.map(record => <ApprovalCard key={record.approvalId} record={record} canDecide={canDecide} />) :
          <div className="approval-empty"><span aria-hidden="true">✓</span><div><h3>没有待处理的审批</h3><p>需要人工决定的请求会出现在这里。</p></div></div>}
      </section>
      {processed.length > 0 && <section className="approval-panel" aria-label="已处理审批"><header><h2>已处理</h2><small>执行前可以撤销</small></header>
        {processed.map(record => <ApprovalCard key={record.approvalId} record={record} canDecide={canDecide} />)}
      </section>}</div>
      <aside className="approval-panel approval-timeline" aria-label="活动时间线"><header><h2>活动时间线</h2></header>
        {events.length ? <ol>{events.map(event => <li key={event.id}><time dateTime={new Date(event.at).toISOString()}>{time(event.at)}</time><b>{event.who}</b><p>{event.text}</p>{'note' in event && event.note && <small>{event.note}</small>}</li>)}</ol> : <p className="approval-empty-timeline">还没有审批活动。</p>}
      </aside>
    </div>}
  </main>;
}
