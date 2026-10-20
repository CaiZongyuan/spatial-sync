import { useClientPlans, useMotionPreview, usePlanDocument } from '../lib/plans/hooks';
import type { SessionClient } from '../lib/stream/client';
import { PlanChecklist, defaultStep } from './PlanChecklist';
import { PreviewNotice } from './PlansPage';

export function useSessionPlan(client: SessionClient, id?: string, stepId?: number) {
  const state = useClientPlans(client);
  const entry = id ? state.entries.find(value => value.plan.id === id) :
    state.entries.find(value => !['completed', 'abandoned'].includes(value.plan.status)) ?? state.entries[0];
  const plan = entry?.plan;
  const metadata = usePlanDocument(plan?.id, plan?.revision, !client.readOnly,
    `${entry?.sceneRevision}:${entry?.epoch}:${entry?.sampleTimeMs}`);
  const selected = plan?.steps.find(step => step.id === stepId) ?? (plan ? defaultStep(plan) : undefined);
  const preview = useMotionPreview(client, plan, metadata.document, selected, String(entry?.sampleTimeMs ?? ''));
  return { ...state, entry, plan, metadata, selected, preview };
}

export function SessionPlans({ client, selection, selectPlan, selectStep }: {
  client: SessionClient; selection: ReturnType<typeof useSessionPlan>;
  selectPlan: (id: string) => void; selectStep: (id: number) => void;
}) {
  const { plan, entry, metadata, selected, preview } = selection;
  return <section className="session-plans" aria-label={client.readOnly ? '录制中的计划' : '会话操作计划'}>
    <header className="session-plan-heading"><h2>{client.readOnly ? '录制中的计划' : '操作计划'}</h2>
      {selection.entries.length > 0 && <select aria-label="选择操作计划" value={plan?.id ?? ''} onChange={event => selectPlan(event.target.value)}>
        {!plan && <option value="">选择计划</option>}{selection.entries.map(value => <option key={value.plan.id} value={value.plan.id}>{value.plan.title} · r{value.plan.revision}</option>)}
      </select>}</header>
    {plan ? <PlanChecklist {...{ client, plan, entry }} document={metadata.document} previous={metadata.previous}
      selected={selected?.id} select={selectStep} metadataError={metadata.error} metadataRetry={metadata.refresh}
      historyError={metadata.historyError} previewControls={<PreviewNotice preview={preview} />} /> :
      <div className="plan-empty">{client.readOnly ? '当前时间片段没有记录操作计划。' : selection.entries.length ? '此会话中没有选定的计划，请重新选择。' : '尚无操作计划。agent 提交后，将在这里逐步显示结果。'}</div>}
    {selection.deviations.length > 0 && <details className="plan-session-deviations"><summary>执行偏差 · {selection.deviations.length}</summary>
      {selection.deviations.map(value => <p key={value.invocationId}>{value.outOfPlan ? '计划外调用' : '跳步执行'} <code>{value.invocationId}</code></p>)}</details>}
  </section>;
}
