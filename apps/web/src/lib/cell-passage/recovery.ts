import type { JsonValue } from '../protocol';
import type { PlanDocument, PlanStep } from '../plans/model';
import { object, passageContract, rows } from './model';

export function liquidAction(action: string): 'aspirate' | 'dispense' | 'mix' | undefined {
  if (passageContract.liquidActions.aspiration.includes(action)) return 'aspirate';
  if (passageContract.liquidActions.dispensing.includes(action)) return 'dispense';
  if (passageContract.liquidActions.mixing.includes(action)) return 'mix';
}

export function operationReceipt(step: PlanStep, snapshot: unknown) {
  return 'do' in step ? rows(object(snapshot)?.operations).find(row => row.operationId === step.do.params.operationId) : undefined;
}

/** A failed resolved intent becomes explicit work, never a second fraction/top-up calculation. */
export function literalRemainder(step: PlanStep, volumeUl: number, action?: 'aspirate' | 'dispense'): PlanStep {
  if (!('do' in step) || !liquidAction(step.do.action) || !Number.isFinite(volumeUl) || volumeUl <= 0)
    throw new Error('请核对原解析目标与当前物料，填写明确的后续液量');
  const direction = liquidAction(step.do.action);
  if (direction === 'mix' && !action) throw new Error('部分混匀须按当前吸头内容明确选择吸液或分液修复');
  return { ...step, do: { entity: step.do.entity, action: action ?? direction!, params: { vessel: step.do.params.vessel, volumeUl } } };
}

export type Replacements = Record<string, Record<number, PlanStep>>;
export function revisePlans(plans: PlanDocument[], owner: string, epoch: number, replacements: Replacements = {}, id: () => string = () => crypto.randomUUID()) {
  const changes = new Map<string, string>();
  const documents = new Map<string, { id: string; title: string; target: 'session'; sessionId: string; steps: PlanStep[] }>();
  for (const plan of plans) {
    const supplied = replacements[plan.id] ?? {};
    if (Object.keys(supplied).some(key => !plan.steps.some(step => step.id === Number(key)))) throw new Error('修订引用未知步骤');
    if (plan.steps.every(step => step.status === 'done')) {
      if (Object.keys(supplied).length) throw new Error('已完成分段不能改写');
      continue;
    }
    const steps = plan.steps.map(original => {
      const replacement = supplied[original.id];
      if (original.status === 'running') throw new Error('先处理在途调用，再修订');
      if (original.status === 'pending' && (plan.stepResults[String(original.id)]?.invocationId || plan.stepResults[String(original.id)]?.invocationIds?.length || plan.stepResults[String(original.id)]?.consumed))
        throw new Error('pending 步骤已有受理证据；只观察原调用，不能重新修订派发');
      if (original.status === 'done' && replacement) throw new Error('已完成步骤不能改写');
      const started = ['failed', 'skipped'].includes(original.status) || !!plan.stepResults[String(original.id)]?.invocationIds?.length || !!plan.stepResults[String(original.id)]?.invocationId;
      if (original.status !== 'done' && started && !replacement) throw new Error('已启动、失败或跳过步骤必须明确修订，不能重放');
      const selected = structuredClone(replacement ?? original);
      if (selected.id !== original.id) throw new Error('修订必须保留步骤身份');
      if (['failed', 'skipped'].includes(original.status) && 'do' in original && passageContract.liquidActions.quantityIntent.includes(original.do.action) &&
          'do' in selected && (!passageContract.liquidActions.literal.includes(selected.do.action) || typeof selected.do.params.volumeUl !== 'number'))
        throw new Error('失败的数量意图必须改为核对后的明确剩余操作，不能再次计算 fraction/all');
      const next = { ...selected, status: 'pending' as const };
      if (original.status === 'done' || !('do' in next)) return next;
      const params: Record<string, JsonValue> = { ...next.do.params, owner, epoch };
      if (replacement || started) {
        params.operationId = id();
        if ('do' in original && typeof original.do.params.operationId === 'string') changes.set(original.do.params.operationId, params.operationId);
      }
      return { ...next, do: { ...next.do, params } };
    });
    documents.set(plan.id, { id: plan.id, title: plan.title, target: 'session', sessionId: plan.sessionId, steps });
  }
  function references(condition: Record<string, JsonValue>) {
    if (Array.isArray(condition.all)) for (const child of condition.all) { const row = object(child); if (row) references(row); }
    if (['completedCycle', 'lastOperationId'].includes(String(condition.property)) && typeof condition.equals === 'string' && changes.has(condition.equals))
      condition.equals = changes.get(condition.equals)!;
  }
  for (const plan of plans) for (const step of documents.get(plan.id)?.steps ?? []) {
    if (plan.steps.find(old => old.id === step.id)?.status === 'done') continue;
    if ('do' in step && step.do.action === 'pellet' && typeof step.do.params.cycle === 'string')
      step.do.params.cycle = changes.get(step.do.params.cycle) ?? step.do.params.cycle;
    for (const field of ['check', 'wait', 'expect'] as const) if (field in step) {
      const condition = object((step as unknown as Record<string, unknown>)[field]); if (condition) references(condition);
    }
  }
  return documents;
}
