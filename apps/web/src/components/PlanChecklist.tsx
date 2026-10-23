import { Link } from '@tanstack/react-router';
import { useState, type ReactNode } from 'react';
import type { OperationPlan } from '../lib/protocol';
import type { SessionClient } from '../lib/stream/client';
import { useInvocations } from '../lib/info/hooks';
import { planChanges, stepCommand, stepEntity, stepInvocationStatus, type PlanDocument, type PlanStep } from '../lib/plans/model';
import type { PlanEntry } from '../lib/plans/store';

const planStates: Record<OperationPlan['status'] | 'rejected', [string, string]> = {
  draft: ['草稿', 'na'], submitted: ['待审批', 'warn'], approved: ['已批准', 'ok'], running: ['运行中', 'run'],
  completed: ['已完成', 'ok'], abandoned: ['已终止', 'na'], rejected: ['已驳回', 'warn'],
};
export function PlanStatus({ status }: { status: OperationPlan['status'] | 'rejected' }) {
  const [label, tone] = planStates[status];
  return <span className={`plan-chip ${tone}`}><i aria-hidden="true" />{label}</span>;
}
function stepLabel(step: PlanStep) {
  return step.status === 'done' ? ('do' in step ? '已完成' : '已通过') :
    ({ pending: '待执行', running: '运行中', failed: '失败', skipped: '已跳过' } as const)[step.status];
}
function StepIcon({ step }: { step: PlanStep }) {
  return <svg className={`plan-step-icon ${step.status} ${'do' in step ? '' : 'condition'}`} viewBox="0 0 20 20" aria-hidden="true">
    <circle className="step-circle" cx="10" cy="10" r="8.4" />
    {step.status === 'done' ? <path className="step-mark" d="M6 10.4l2.7 2.6L14 7.5" /> :
      step.status === 'running' ? <><path className="step-spinner" d="M10 1.6a8.4 8.4 0 0 1 8.4 8.4" /><circle className="step-dot" cx="10" cy="10" r="2.5" /></> :
        step.status === 'failed' ? <path className="step-mark" d="M7 7l6 6M13 7l-6 6" /> :
          step.status === 'skipped' ? <path className="step-mark" d="M6 10h8" /> : <text x="10" y="13.2" textAnchor="middle">{step.id}</text>}
  </svg>;
}

export function defaultStep(plan: OperationPlan) {
  return plan.steps.find(step => step.status === 'running') ?? plan.steps.find(step => step.status === 'pending') ?? plan.steps.at(-1);
}

export function PlanChecklist({ client, plan, entry, document, previous, selected, select, metadataError,
  metadataRetry, historyError, fullPage = false, previewControls }: {
  client: SessionClient; plan: OperationPlan; entry?: PlanEntry; document?: PlanDocument; previous?: PlanDocument;
  selected?: number; select: (step: number) => void; metadataError?: string; metadataRetry?: () => void;
  historyError?: string; fullPage?: boolean; previewControls?: ReactNode;
}) {
  const invocations = useInvocations(client.info);
  const actualMetadata = document?.id === plan.id && document.revision === plan.revision ? document : undefined;
  const changes = previous && previous.revision === plan.revision - 1 ? planChanges(previous, plan)
    : actualMetadata?.changes ?? entry?.changes;
  const active = plan.steps.find(step => step.id === selected) ?? defaultStep(plan);
  const shell = typeof navigator !== 'undefined' && /win/i.test(navigator.platform) ? 'powershell' : 'bash';
  const command = active ? stepCommand(plan, active, shell) : '';
  const [copy, setCopy] = useState<{ command: string; error?: string }>();
  const done = plan.steps.filter(step => step.status === 'done').length;
  return <section className="plan-checklist" aria-label="操作计划" data-testid="plan-panel">
    <header className="plan-checklist-heading"><div><span className="eyebrow">操作计划 · r{plan.revision}</span><h2>{plan.title}</h2></div><PlanStatus status={plan.status} /></header>
    <div className="plan-identity"><code>{plan.id}</code><span>{done} / {plan.steps.length} 步已完成</span>
      {!fullPage && !client.readOnly && <Link to="/plans/$planId" params={{ planId: plan.id }}>打开计划 ↗</Link>}</div>
    <dl className="plan-metadata"><div><dt>提交者</dt><dd>{actualMetadata ? `${actualMetadata.requestedBy.kind} · ${actualMetadata.requestedBy.name}` :
      client.readOnly ? '录制未包含' : '正在读取…'}</dd></div><div><dt>审批</dt><dd>{actualMetadata ? actualMetadata.approvalMode === 'auto' ? '自动批准' :
        `人工审批 · ${actualMetadata.approvalStatus ?? '状态未知'}` : client.readOnly ? '录制未包含' : '正在读取…'}</dd></div></dl>
    {metadataError && <p role="status" className="plan-inline-error">计划详情暂不可用：{metadataError} <button onClick={metadataRetry}>重试</button></p>}
    {actualMetadata?.persistence.status === 'failed' && <p className="plan-inline-error" role="alert">计划历史未能保存，请检查网关。</p>}
    {plan.revision > 1 && <div className="plan-revision-note" data-testid="plan-revision-diff"><span aria-hidden="true">↳</span><span>
      r{plan.revision - 1} → r{plan.revision}：{changes === undefined ? '缺少上一修订，无法比较步骤变化。' : changes.length ?
        changes.map(change => change.change === 'title' ? '名称变更' : `${({ added: '新增', removed: '移除', changed: '修改', moved: '调整顺序' } as const)[change.change]}步骤 ${change.stepId}`).join('；') : '步骤内容未改变。'}
      {historyError && <small>上一修订详情暂不可用。</small>}</span></div>}
    <ol className="plan-steps" aria-label="步骤清单">{plan.steps.map(step => {
      const result = actualMetadata?.stepResults[String(step.id)];
      const association = entry?.associations[step.id];
      const ids = [...new Set([...(result?.invocationIds ?? (result?.invocationId ? [result.invocationId] : [])), ...(association?.invocationIds ?? [])])];
      const invocation = invocations.find(value => value.invocationId === ids.at(-1));
      const progress = invocation?.progress;
      const added = changes?.some(change => change.stepId === step.id && change.change === 'added');
      const description = 'do' in step ? step.do.action : 'wait' in step ? '等待条件' : '检查条件';
      const params = 'do' in step ? step.do.params : 'wait' in step ? step.wait : step.check;
      return <li key={step.id} className={`plan-step ${step.status}`} data-testid={`plan-step-${step.id}`} data-status={step.status}>
        <button type="button" className={`plan-step-select ${active?.id === step.id ? 'is-selected' : ''}`}
          aria-pressed={active?.id === step.id} aria-label={`步骤 ${step.id} ${description} ${stepLabel(step)}`}
          onClick={() => select(step.id)} onFocus={() => select(step.id)}>
          <StepIcon step={step} /><span className="plan-step-copy"><span className="plan-step-title"><b>{step.id} · {description}</b>
            {added && <span className="plan-tag accent">r{plan.revision} 新增</span>}<span className={`plan-step-status ${step.status}`}>{stepLabel(step)}</span></span>
            <code className="plan-step-entity">{stepEntity(step)}</code><code className="plan-step-params">{JSON.stringify(params)}</code>
            {'do' in step && step.expect && <small>期望 {JSON.stringify(step.expect)}</small>}
            {(association?.outOfOrder || result?.outOfOrder) && <small className="plan-deviation">跳步执行</small>}
            {step.status === 'running' && (typeof progress === 'number' ? <><progress max="1" value={progress} aria-label={`步骤 ${step.id} 进度`} /><small>进度 {Math.round(progress * 100)}%</small></> : <small>运行中 · 进度尚未提供</small>)}
          </span>
        </button>
        {ids.length > 0 && <details className="plan-associated"><summary>关联调用 · {ids.length}</summary>{ids.map(id => {
          const value = invocations.find(item => item.invocationId === id);
          return <div key={id}><code>{id}</code><span>{stepInvocationStatus(result, id, value) ?? '调用状态未知（当前快照未包含）'}</span>
            {value?.error && <p>{value.error.message}</p>}</div>;
        })}</details>}
        {client.readOnly && 'do' in step && step.status !== 'pending' && !ids.length && <small className="plan-association-unknown">此片段未记录调用关联</small>}
      </li>;
    })}</ol>
    {previewControls}
    {active && command && <div className="plan-command-block"><div className="eyebrow">当前步骤命令 · {shell === 'powershell' ? 'PowerShell' : 'Shell'}</div>
      <div className="plan-command"><span aria-hidden="true">$</span><code data-testid="plan-command">{command}</code><button type="button"
        onClick={async () => { try { await navigator.clipboard.writeText(command); setCopy({ command }); }
          catch { setCopy({ command, error: '复制失败，可手动选择命令。' }); } }}>{copy?.command === command && !copy.error ? '已复制' : '复制'}</button></div>
      {copy?.command === command && copy.error && <small role="status">{copy.error}</small>}
      <p>由 agent 逐步执行，平台记录实际结果。{client.readOnly && ' 回放不执行命令。'}</p></div>}
  </section>;
}
