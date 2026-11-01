import type { PlanDocument } from '../plans/model';
import { passageContract } from './model';

export function roundIdentity(id: string) {
  const match = /^cp-(.+)-stage([1-7])-part([1-9]\d*)-of([1-9]\d*)$/.exec(id);
  return match ? { roundId: match[1], stage: Number(match[2]), part: Number(match[3]), parts: Number(match[4]) } : undefined;
}
export function passageRound(selected: string, documents: PlanDocument[]) {
  const identity = roundIdentity(selected);
  if (!identity) { if (selected.startsWith('cp-')) throw new Error('本轮分段身份格式未知，禁止按单个普通计划执行'); return undefined; }
  const parts = documents.filter(plan => roundIdentity(plan.id)?.roundId === identity.roundId).sort((a, b) => {
    const left = roundIdentity(a.id)!, right = roundIdentity(b.id)!; return left.stage - right.stage || left.part - right.part;
  });
  for (const stage of passageContract.stages) {
    const rows = parts.filter(plan => roundIdentity(plan.id)!.stage === stage.id);
    if (!rows.length) throw new Error('本轮缺少阶段计划，禁止推进');
    const total = roundIdentity(rows[0].id)!.parts;
    if (rows.length !== total || rows.some((plan, index) => {
      const part = roundIdentity(plan.id)!;
      return part.part !== index + 1 || part.parts !== total ||
        !plan.title.startsWith(`Cell Passage ${identity.roundId} · ${stage.id}/7 · `) || !plan.title.endsWith(` · part ${part.part}/${total}`);
    })) throw new Error('阶段分段缺失或总数冲突，禁止推进');
    if (rows.some(plan => !plan.steps.length || plan.steps.length > passageContract.roundPlanIdentity.maxPartSteps ||
      plan.steps.some(step => step.id < stage.firstStep || step.id > stage.lastStep))) throw new Error('阶段步骤范围或分段规模错误');
  }
  const first = parts[0];
  const stepIds = parts.flatMap(plan => plan.steps.map(step => step.id));
  const operationIds = parts.flatMap(plan => plan.steps.flatMap(step => 'do' in step ? [step.do.params.operationId] : []));
  if (new Set(stepIds).size !== stepIds.length || operationIds.some(id => typeof id !== 'string' || !id) || new Set(operationIds).size !== operationIds.length)
    throw new Error('本轮全局步骤或操作身份重复/未知');
  if (parts.some(plan => plan.sessionId !== first.sessionId || plan.sceneRevision !== first.sceneRevision || plan.epoch !== first.epoch || plan.target !== 'session')) throw new Error('分段会话或场景身份不一致');
  if (parts.some(plan => !['approved', 'running', 'completed'].includes(plan.status))) throw new Error('本轮有未批准或已中断分段');
  if (parts.some(plan => plan.status === 'completed' && plan.steps.some(step => step.status !== 'done'))) throw new Error('分段完成证据不一致');
  const next = parts.findIndex(plan => plan.status !== 'completed');
  if (next >= 0 && parts.slice(next + 1).some(plan => plan.status !== 'approved' || plan.steps.some(step => step.status !== 'pending'))) throw new Error('分段已乱序推进，必须核对');
  return { roundId: identity.roundId, parts, next: next < 0 ? undefined : parts[next] };
}
