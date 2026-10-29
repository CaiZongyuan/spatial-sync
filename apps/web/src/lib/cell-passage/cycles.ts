import type { EntityValues, Invocation, JsonValue } from '../protocol';
import type { PlanDocument, PlanStep } from '../plans/model';
import { object, passageContract } from './model';

export interface CycleBatch { stage: number; status: 'cycle_receipt_valid' | 'invalid' | 'unavailable'; errors: string[]; invocationId?: string; receipt?: Record<string, JsonValue>; stageSnapshotAvailable: boolean }
export interface CycleEvidence { status: 'cycle_evidence_valid' | 'invalid' | 'unavailable'; batches: CycleBatch[] }
type Target = { sessionId: string; sceneRevision: string; epoch: number };
type Read = <T>(path: string) => Promise<T>;
const canonical = (value: unknown) => JSON.stringify(value, (_key, row) => row && typeof row === 'object' && !Array.isArray(row) ? Object.fromEntries(Object.entries(row).sort(([a], [b]) => a.localeCompare(b))) : row);

export function auditCycle(invocation: Invocation, step: PlanStep, expectedLoad: string[], stage: number, snapshot?: EntityValues): CycleBatch {
  const report: CycleBatch = { stage, status: 'unavailable', errors: [], invocationId: invocation.invocationId, stageSnapshotAvailable: !!snapshot };
  const receipt = object(invocation.result), contract = passageContract.centrifugeCycleReceipt;
  if (!receipt || receipt.schema !== contract.schema) return { ...report, errors: ['源侧实际离心完成 receipt 不可用，不能从 Plan done 推造'] };
  if (!('do' in step)) return { ...report, errors: ['原离心动作不可用'] };
  report.receipt = receipt;
  const params = invocation.params;
  if (invocation.status !== 'succeeded' || invocation.action !== 'start' || invocation.entityKey !== step.do.entity ||
      canonical(params) !== canonical(step.do.params) || typeof params.owner !== 'string' || !params.owner || !Number.isInteger(params.epoch) || Number(params.epoch) < 0 ||
      typeof params.operationId !== 'string' || !params.operationId || !invocation.invocationId ||
      receipt.entityKey !== invocation.entityKey || receipt.invocationId !== invocation.invocationId || receipt.operationId !== params.operationId ||
      receipt.completedCycle !== params.operationId || receipt.owner !== params.owner || receipt.epoch !== params.epoch ||
      receipt.completedSimTime !== invocation.endedSimTime || receipt.rotorStopped !== true || receipt.lidClosed !== true)
    report.errors.push('完成 receipt 的原调用身份、成功终态或停稳/关盖证据不一致');
  const slots = receipt.completedLoad;
  if (!Array.isArray(slots) || slots.length !== 4 || slots.some(row => row !== null && typeof row !== 'string') ||
      canonical(slots.filter(row => row !== null).sort()) !== canonical([...expectedLoad].sort())) report.errors.push('实际完成装载与本批四支容器不一致');
  const seconds = step.do.params.durationSeconds;
  const durationKnown = typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0;
  const minimum = durationKnown ? Math.max(contract.minimumRunSeconds, seconds) : Infinity;
  const run = receipt.cycleRunSeconds, turns = receipt.actualRotorTurns, rpm = receipt.actualRpm, completed = receipt.completedSimTime;
  if (!durationKnown || [run, turns, rpm, completed].some(value => typeof value !== 'number' || !Number.isFinite(value)) ||
      Number(run) + 1e-9 < minimum || Number(turns) <= 0 || Number(rpm) < 0 || Number(rpm) >= contract.stoppedSpeedRadPerSecond * 60 / (2 * Math.PI) || Number(completed) < 0)
    report.errors.push('实际运行时间、转角或停稳反馈不满足原要求');
  if (snapshot && ['completedCycle', 'completedLoad', 'cycleRunSeconds', 'actualRotorTurns'].some(key => canonical(snapshot.values[key]) !== canonical(receipt[key])))
    report.errors.push('本浏览器保存的阶段现场与可靠完成 receipt 不一致');
  return { ...report, status: report.errors.length ? 'invalid' : 'cycle_receipt_valid' };
}

/** Only existing Plan/invocation GETs; missing or evicted evidence never dispatches another cycle. */
export async function collectCycleEvidence(read: Read, route: string, plans: PlanDocument[], bodies: Record<string, JsonValue>, target: Target,
  stageStates: Map<number, Record<string, EntityValues>>, entity = '/World/Lab/Centrifuge'): Promise<CycleEvidence> {
  const batches = await Promise.all(passageContract.centrifugeCycleReceipt.stages.map(async stage => {
    try {
      const definition = passageContract.stages.find(row => row.id === stage)!;
      const calls = plans.flatMap(plan => plan.steps.filter(step => step.id >= definition.firstStep && step.id <= definition.lastStep &&
        'do' in step && step.do.entity === entity && step.do.action === 'start').map(step => ({ plan, step })));
      if (calls.length !== 1) throw new Error('每批需要唯一 Plan 绑定的离心 start');
      const { plan, step } = calls[0];
      if (plan.sessionId !== target.sessionId || plan.sceneRevision !== target.sceneRevision || plan.epoch !== target.epoch || step.status !== 'done') throw new Error('离心 Plan 不属于当前已完成会话上下文');
      const identity = plan.stepResults[String(step.id)]?.invocationId;
      if (!identity) throw new Error('离心完成调用绑定缺失');
      const invocation = await read<Invocation>(`${route}/invocations/${encodeURIComponent(identity)}`);
      if (invocation.invocationId !== identity || invocation.epoch !== target.epoch) throw new Error('历史调用身份或 session epoch 不匹配');
      const names = stage === 3 ? ['S1', 'S2', 'S3', 'S4'] : ['S5', 'S6', 'B5', 'B6'];
      const load = names.map(name => bodies[name]);
      if (load.some(key => typeof key !== 'string' || !key) || new Set(load).size !== 4) throw new Error('固定场景装载身份未知');
      return auditCycle(invocation, step, load as string[], stage, stageStates.get(stage)?.[entity]);
    } catch (error) {
      return { stage, status: 'unavailable' as const, errors: [error instanceof Error ? error.message : String(error)], stageSnapshotAvailable: !!stageStates.get(stage)?.[entity] };
    }
  }));
  return { status: batches.every(batch => batch.status === 'cycle_receipt_valid') ? 'cycle_evidence_valid' : batches.some(batch => batch.status === 'invalid') ? 'invalid' : 'unavailable', batches };
}
