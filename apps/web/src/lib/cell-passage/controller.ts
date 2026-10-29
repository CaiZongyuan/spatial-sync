import { ApiError, request } from '../api';
import type { Catalog, Invocation, JsonValue } from '../protocol';
import type { PlanDocument, PlanStep } from '../plans/model';
import { materialState, object, passageContract } from './model';
import type { EntityValues } from '../protocol';

type Session = { sessionId: string; sceneRevision: string; epoch: number; state: string; simTime: number };
type Transport = <T>(path: string, options?: RequestInit) => Promise<T>;
export interface MechanicalFact { entity: string; property: string; value: JsonValue }
export interface Review { token: string; facts: MechanicalFact[] }
export interface Checkpoint { session: Session; plan: PlanDocument; entity: EntityValues }
const terminal = (status: string) => ['succeeded', 'failed', 'cancelled', 'rejected'].includes(status);
const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'X-Actor': 'user:cell-passage-web' }, body: JSON.stringify(body) });
function failure(message: string): never { throw new Error(message); }

/** One browser process schedules a pinned Plan. Unknown admission is observed, never replayed. */
export class PassageController {
  readonly owner = `web-${crypto.randomUUID()}`;
  private active?: { stepId: number; invocationId?: string; startedSimTime: number };
  private pinned?: { sceneRevision: string; epoch: number; revision: number; controlEpoch: number; controlMode: string; materialRevision: number };
  private settlingSince?: number;
  private reviewedCheckpoint?: string;
  private busy = false;
  private stopped = true;
  constructor(readonly sessionId: string, readonly workcell: string, private http: Transport = request) {}
  private route = () => `/sessions/${encodeURIComponent(this.sessionId)}`;
  stop() { this.stopped = true; }
  get dispatching() { return !this.stopped; }
  get observing() { return !!this.active; }
  async inspect(planId: string): Promise<Checkpoint> {
    const [session, plan, state] = await Promise.all([
      this.http<Session>(this.route()), this.http<PlanDocument>(`/plans/${encodeURIComponent(planId)}`),
      this.http<{ entities: Record<string, EntityValues> }>(`${this.route()}/state?keys=${encodeURIComponent(this.workcell)}`),
    ]);
    const entity = state.entities[this.workcell];
    if (!entity) failure('工作单元状态未知');
    return { session, plan, entity };
  }
  private valid(checkpoint: Checkpoint) {
    const { session, plan, entity } = checkpoint;
    const material = materialState(entity);
    if (plan.sessionId !== this.sessionId || plan.sceneRevision !== session.sceneRevision || plan.epoch !== session.epoch) failure('会话已改变；请另开一轮');
    if (!material.reconciled || material.stale || entity.values.auditPassed !== true) failure('物料未知、过期或未通过核对，禁止派发');
    if (['controlOwner', 'controlEpoch', 'controlMode', 'controlSafe', 'materialRevision', 'auditPassed'].some(key => entity.meta[key]?.stale === true)) failure('控制或物料读数已过期');
    if (typeof entity.values.controlOwner !== 'string' || !Number.isInteger(entity.values.controlEpoch) || !Number.isInteger(entity.values.materialRevision)) failure('控制或物料修订未知');
    if (!['approved', 'running', 'completed'].includes(plan.status)) failure('计划尚未批准或已经中断');
    return entity.values;
  }
  async requestControl(planId: string, mode: 'manual' | 'auto') {
    this.stop();
    const checkpoint = await this.inspect(planId);
    const values = this.valid(checkpoint);
    if (typeof values.controlOwner !== 'string' || !Number.isInteger(values.controlEpoch)) failure('控制状态未知');
    if (values.controlMode === 'holding') failure('已有交接请求，先核对并确认');
    // Source accept fences the old epoch even when the simulation is paused.
    return this.http<Invocation>(`${this.route()}/actions`, post({ requestId: crypto.randomUUID(), entityKey: this.workcell,
      action: 'requestControl', params: { owner: values.controlOwner, epoch: values.controlEpoch,
        operationId: crypto.randomUUID(), nextOwner: this.owner, mode } }));
  }
  private async pendingControlled() {
    const lists = await Promise.all(['pending_approval', 'accepted', 'running'].map(status =>
      this.http<{ invocations: Invocation[] }>(`${this.route()}/invocations?status=${status}&limit=1000`)));
    if (lists.some(list => list.invocations.length >= 1000)) failure('在途调用列表超出核对范围，禁止交接');
    return lists.flatMap(list => list.invocations).filter(invocation =>
      !['requestControl', 'confirmControl'].includes(invocation.action) && typeof invocation.params?.operationId === 'string');
  }
  async confirmControl(planId: string) {
    const checkpoint = await this.inspect(planId);
    const values = this.valid(checkpoint);
    if (values.controlMode !== 'holding' || values.controlSafe !== true) failure('设备尚未实测停稳；保持现场并等待');
    if ((await this.pendingControlled()).length) failure('仍有已受理或运行调用；先取消并观察终态，再确认交接');
    const requests = object(values.materialSnapshot)?.controlRequests;
    const last = Array.isArray(requests) ? object(requests.at(-1)) : undefined;
    const params = typeof last?.paramsJson === 'string' ? JSON.parse(last.paramsJson) as Record<string, unknown> : undefined;
    if (last?.action !== 'requestControl' || params?.nextOwner !== this.owner) failure('该交接不属于当前浏览器；不能确认');
    return this.http<Invocation>(`${this.route()}/actions`, post({ requestId: crypto.randomUUID(), entityKey: this.workcell,
      action: 'confirmControl', params: { owner: values.controlOwner, epoch: values.controlEpoch, operationId: crypto.randomUUID() } }));
  }
  async cancelActive(planId: string) {
    this.stop();
    const { plan } = await this.inspect(planId);
    const controlled = await this.pendingControlled();
    const ids = new Set([...controlled.map(row => row.invocationId), ...plan.steps.filter(step => step.status === 'running').flatMap(step => {
      const result = plan.stepResults[String(step.id)];
      return result?.invocationIds ?? (result?.invocationId ? [result.invocationId] : []);
    })]);
    for (const id of ids) {
      const invocation = await this.http<Invocation>(`${this.route()}/invocations/${encodeURIComponent(id)}`);
      if (!terminal(invocation.status)) await this.http(`${this.route()}/invocations/${encodeURIComponent(id)}/cancel`, post({}));
    }
  }
  private checkpointToken(checkpoint: Checkpoint) {
    const { session, plan, entity } = checkpoint;
    return JSON.stringify([session.sessionId, session.sceneRevision, session.epoch, plan.revision,
      entity.values.controlOwner, entity.values.controlEpoch, entity.values.controlMode,
      entity.values.controlSafe, entity.values.materialRevision]);
  }
  async review(planId: string, catalog: Catalog): Promise<Review> {
    this.stop();
    const checkpoint = await this.inspect(planId);
    const values = this.valid(checkpoint);
    if (values.controlOwner !== this.owner || !['auto', 'manual'].includes(String(values.controlMode)) || values.controlSafe !== true) failure('先完成浏览器控制交接并停稳');
    const binding = catalog.entities[this.workcell];
    if (!binding || !Array.isArray(binding.config.managedDevices) || !Array.isArray(binding.config.vessels)) failure('机械核对配置缺失');
    const keys = [...new Set([this.workcell, ...binding.config.managedDevices.map(String),
      ...binding.config.vessels.map(row => String(object(row)?.entityKey ?? ''))])];
    if (!keys.length || keys.some(key => !key || !catalog.entities[key])) failure('机械核对范围不完整');
    const state = await this.http<{ entities: Record<string, EntityValues> }>(`${this.route()}/state?keys=${encodeURIComponent(keys.join(','))}`);
    const facts: MechanicalFact[] = [];
    for (const key of keys) {
      const schema = catalog.types[catalog.entities[key].type];
      const contract = (passageContract.reconciliation as Record<string, { observedProperties: string[]; safeEquals: Record<string, JsonValue> }>)[catalog.entities[key].type];
      if (!contract) failure(`未知机械类型，不能核对续行：${catalog.entities[key].type}`);
      const properties = contract.observedProperties.filter(name => name in (schema?.properties ?? {}));
      if (!properties.length) failure(`缺少机械核对字段：${key}`);
      for (const property of properties) {
        const entity = state.entities[key];
        if (!entity || !(property in entity.values) || entity.meta[property]?.stale === true) failure(`机械事实未知或过期：${key} ${property}`);
        if (property in contract.safeEquals && entity.values[property] !== contract.safeEquals[property]) failure(`设备尚未停稳：${key}`);
        facts.push({ entity: key, property, value: entity.values[property] });
      }
    }
    const token = this.checkpointToken(checkpoint);
    if (this.checkpointToken(await this.inspect(planId)) !== token) failure('核对期间现场已变化，请重新检查');
    return { token, facts };
  }
  async prepareRemaining(planId: string, review: Review, replacements: Record<number, PlanStep> = {}) {
    this.stop();
    const checkpoint = await this.inspect(planId);
    const values = this.valid(checkpoint);
    if (checkpoint.plan.steps.some(step => step.status === 'running')) failure('在途步骤未结束，禁止修订');
    if (this.active) {
      this.active.invocationId ??= checkpoint.plan.stepResults[String(this.active.stepId)]?.invocationId;
      if (!this.active.invocationId) failure('受理结果仍未知；保留现场，禁止重放');
      const invocation = await this.http<Invocation>(`${this.route()}/invocations/${encodeURIComponent(this.active.invocationId)}`);
      if (!terminal(invocation.status)) failure('在途调用未结束，禁止修订');
      this.active = undefined;
    }
    for (const step of checkpoint.plan.steps) {
      if (['failed', 'skipped'].includes(step.status) && (!replacements[step.id] || replacements[step.id].id !== step.id)) failure('失败步骤必须明确修订；不能按原量重新执行');
    }
    if (values.controlOwner !== this.owner || !['auto', 'manual'].includes(String(values.controlMode)) || values.controlSafe !== true) failure('先取得浏览器控制并确认停稳');
    if (!review?.facts.length || this.checkpointToken(checkpoint) !== review.token) failure('先核对当前机械事实与物料修订');
    for (const fact of review.facts) {
      const result = await this.http<{ satisfied: boolean }>(`${this.route()}/check`, post({ key: fact.entity,
        property: fact.property, op: 'equals', value: fact.value }));
      if (!result.satisfied) failure('核对事实已变化，禁止修订续行');
    }
    if (this.checkpointToken(await this.inspect(planId)) !== review.token) failure('核对期间现场已变化，禁止续行');
    const plan = checkpoint.plan;
    const steps = plan.steps.map(step => {
      const next = { ...(['failed', 'skipped'].includes(step.status) ? replacements[step.id] : step), status: 'pending' };
      if (step.status === 'done' || !('do' in next)) return next;
      return { ...next, do: { ...next.do, params: { ...next.do.params, owner: this.owner,
        epoch: values.controlEpoch, operationId: crypto.randomUUID() } } };
    });
    const revised = await this.http<PlanDocument>(`/plans/${encodeURIComponent(planId)}/revisions`, post({ id: plan.id, kind: 'operation',
      target: plan.target, title: plan.title, sessionId: this.sessionId, status: 'draft', steps }));
    const current = await this.inspect(planId);
    if (current.plan.revision !== revised.revision || current.entity.values.materialRevision !== values.materialRevision ||
        current.entity.values.controlOwner !== this.owner || current.entity.values.controlEpoch !== values.controlEpoch) failure('修订后现场变化，请重新核对');
    this.reviewedCheckpoint = this.checkpointToken(current);
    return revised;
  }
  async pause() {
    this.stop();
    return this.http(`${this.route()}/commands`, post({ requestId: crypto.randomUUID(), command: 'pause' }));
  }
  async resume() {
    return this.http(`${this.route()}/commands`, post({ requestId: crypto.randomUUID(), command: 'resume' }));
  }
  async arm(planId: string, mode: 'auto' | 'manual' = 'auto') {
    const checkpoint = await this.inspect(planId);
    const values = this.valid(checkpoint);
    if (values.controlOwner !== this.owner || values.controlMode !== mode || values.controlSafe !== true) failure('先取得相应浏览器控制并等待停稳');
    if (!this.reviewedCheckpoint || this.reviewedCheckpoint !== this.checkpointToken(checkpoint)) failure('先读取现场核对并准备后续步骤');
    if (checkpoint.plan.steps.some(step => ['running', 'failed'].includes(step.status)) || this.active) failure('先观察或取消在途调用，并修订失败步骤');
    if (typeof values.materialRevision !== 'number' || typeof values.controlEpoch !== 'number') failure('修订状态未知');
    this.pinned = { sceneRevision: checkpoint.session.sceneRevision, epoch: checkpoint.session.epoch, revision: checkpoint.plan.revision,
      controlEpoch: values.controlEpoch, controlMode: mode, materialRevision: values.materialRevision };
    this.stopped = false;
  }
  async advance(planId: string) {
    if (this.busy) return '正在处理上一请求';
    this.busy = true;
    try { return await this.advanceOnce(planId); } finally { this.busy = false; }
  }
  private async advanceOnce(planId: string): Promise<string> {
    const checkpoint = await this.inspect(planId);
    const { session, plan } = checkpoint;
    const values = this.valid(checkpoint);
    // Always observe accepted work, including after owner loss or a local stop.
    if (this.active) {
      this.active.invocationId ??= plan.stepResults[String(this.active.stepId)]?.invocationId;
      if (!this.active.invocationId) { this.stop(); return '受理结果未知；禁止重发，保留本轮并检查调用'; }
      const invocation = await this.http<Invocation>(`${this.route()}/invocations/${encodeURIComponent(this.active.invocationId)}`);
      if (!terminal(invocation.status)) {
        if (session.simTime - this.active.startedSimTime > 120) { this.stop(); return '仿真时间预算超限；检查或取消在途调用'; }
        return `观察已受理调用：${invocation.status}`;
      }
      const updated = await this.http<PlanDocument>(`/plans/${encodeURIComponent(planId)}`);
      if (invocation.status !== 'succeeded' || updated.steps.find(step => step.id === this.active?.stepId)?.status !== 'done') {
        this.stop(); return '调用未成功或计划期望未通过；保留现场并修订后续步骤';
      }
      this.active = undefined;
      if (this.pinned) this.pinned.materialRevision = Number(values.materialRevision);
      this.reviewedCheckpoint = this.checkpointToken(checkpoint);
      return '当前步骤已完成';
    }
    if (this.stopped || !this.pinned) return '已停止派发；核对后继续';
    const pinned = this.pinned;
    if (session.sceneRevision !== pinned.sceneRevision || session.epoch !== pinned.epoch || plan.revision !== pinned.revision ||
        values.controlOwner !== this.owner || values.controlEpoch !== pinned.controlEpoch || values.controlMode !== pinned.controlMode ||
        values.materialRevision !== pinned.materialRevision) { this.stop(); return '控制、物料或计划已改变；停止派发，必须重新核对'; }
    if (session.state === 'PAUSED') return '仿真暂停；等待与机械动作均冻结';
    if (session.state !== 'RUNNING') { this.stop(); return '会话已中断；不能作为执行检查点'; }
    if (plan.status === 'completed') { this.stop(); return '当前计划完成；整轮传代仍待全部阶段与结果核验'; }
    if (values.controlSafe !== true) {
      this.settlingSince ??= session.simTime;
      if (session.simTime - this.settlingSince > 120) { this.stop(); return '设备停稳超出仿真时间预算；保留现场并核对'; }
      return '设备正在停稳；继续观察源侧安全状态';
    }
    this.settlingSince = undefined;
    const step = plan.steps.find(row => !['done', 'skipped'].includes(row.status));
    if (!step || step.status !== 'pending') failure('下一步骤不能执行；先检查并修订');
    if ('wait' in step) failure('实验等待必须使用工作单元仿真时间动作');
    const context = { planId, planRevision: plan.revision, stepId: step.id };
    if ('check' in step) {
      const condition = step.check;
      const operator = Object.keys(condition).find(key => !['entity', 'property', 'message', 'by'].includes(key));
      if (!operator) failure('检查条件无效');
      this.stop(); // A lost check result is also not replayed. Re-arm only after authoritative Plan inspection.
      const result = await this.http<{ satisfied: boolean }>(`${this.route()}/check`, post({ key: condition.entity,
        property: condition.property, op: operator, value: condition[operator!], ...(condition.by !== undefined ? { by: condition.by } : {}), ...context }));
      this.stopped = !result.satisfied;
      return result.satisfied ? '检查已通过' : '检查未通过；停止派发';
    }
    const params = step.do.params;
    if (params.owner !== this.owner || params.epoch !== pinned.controlEpoch || typeof params.operationId !== 'string' || !params.operationId) failure('计划控制身份已过期；核对后修订剩余步骤');
    this.active = { stepId: step.id, startedSimTime: session.simTime };
    try {
      const invocation = await this.http<Invocation>(`${this.route()}/actions`, post({ requestId: crypto.randomUUID(), entityKey: step.do.entity,
        action: step.do.action, params, wait: false, ...context }));
      this.active.invocationId = invocation.invocationId;
      return '请求已受理；正在观察执行结果';
    } catch (error) {
      this.stop();
      if (error instanceof ApiError) { this.active = undefined; throw error; }
      return '请求响应丢失；查询已知计划调用，禁止重放';
    }
  }
}
