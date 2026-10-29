import { ApiError, request } from '../api';
import type { Catalog, Invocation, JsonValue, SceneManifest, Snapshot } from '../protocol';
import type { PlanDocument, PlanStep } from '../plans/model';
import { materialState, object, passageContract, rows } from './model';
import type { EntityValues } from '../protocol';
import { liquidAction, operationReceipt, revisePlans } from './recovery';
import { passageRound, roundIdentity } from './round';
import { auditConservation, auditFinal, type FinalAudit } from './audit';
import { compareFreshInitial, configurationInitial, type ConfigurationInitial } from './configuration';
import { collectCycleEvidence } from './cycles';

type Session = { sessionId: string; sceneRevision: string; epoch: number; state: string; simTime: number; sceneVersion?: string; sceneContentHash?: string };
type Transport = <T>(path: string, options?: RequestInit) => Promise<T>;
export interface MechanicalFact { entity: string; property: string; value: JsonValue; verify?: boolean }
type PublicPose = { p: number[]; q: number[]; epoch: number; sceneRevision: string };
export interface Review { token: string; facts: MechanicalFact[]; stamp?: { materialRevision: number; controlEpoch: number; planRevision: number }; staticPoses?: { entity: string; site: string; pose: PublicPose }[] }
export interface Checkpoint { session: Session; plan: PlanDocument; entity: EntityValues }
export interface PassageFlow { phase: 'acquiring' | 'paused-hold' | 'review' | 'preparing' | 'ready' | 'running' | 'manual' | 'paused' | 'blocked'; planId: string; mode: 'auto' | 'manual'; single: boolean; catalog: Catalog; needsReview: boolean; beganSimTime: number; review?: Review }
const terminal = (status: string) => ['succeeded', 'failed', 'cancelled', 'rejected'].includes(status);
const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'X-Actor': 'user:cell-passage-web' }, body: JSON.stringify(body) });
function failure(message: string): never { throw new Error(message); }

/** One browser process schedules a pinned Plan. Unknown admission is observed, never replayed. */
export class PassageController {
  readonly owner = `web-${crypto.randomUUID()}`;
  private active?: { stepId: number; invocationId?: string; startedSimTime: number; terminalFailure?: boolean; uncertainStopGeneration?: number };
  private pinned?: { sceneRevision: string; epoch: number; revision: number; controlEpoch: number; controlMode: string; materialRevision: number };
  private settlingSince?: number;
  private reviewedCheckpoints = new Map<string, { revision: number; token: string }>();
  private round?: { current: string; revisions: Map<string, number> };
  private revisionUncertain = false;
  private initialMaterial?: JsonValue;
  private expectedInitial?: ConfigurationInitial;
  private initialComparison?: string[];
  private auditBefore?: Snapshot;
  private stageStates = new Map<number, Record<string, EntityValues>>();
  finalAudit?: FinalAudit;
  flow?: PassageFlow;
  private busy = false;
  private flowBusy = false;
  private cancellationRequests = new Set<string>();
  private stopped = true;
  private stopGeneration = 0;
  constructor(readonly sessionId: string, readonly workcell: string, private http: Transport = request) {}
  private route = () => `/sessions/${encodeURIComponent(this.sessionId)}`;
  stop() { this.stopped = true; this.stopGeneration++; }
  get dispatching() { return !this.stopped; }
  get observing() { return !!this.active; }
  get currentPlanId() { return this.flow?.planId ?? this.round?.current; }
  get initialSummary() {
    if (!this.expectedInitial) return undefined;
    return { basis: this.expectedInitial.basis, volumeUl: rows(this.expectedInitial.material.vessels).reduce((sum, row) => sum + Number(row.volumeUl), 0),
      fresh: this.initialComparison === undefined ? 'not-observed' : this.initialComparison.length ? 'mismatched' : 'matched' };
  }
  get polling() { return this.flow ? ['acquiring', 'running'].includes(this.flow.phase) : this.dispatching || this.observing; }
  block() { this.stop(); if (this.flow) this.flow = { ...this.flow, phase: 'blocked' }; }
  private async captureFinal(checkpoint: Checkpoint, plans: PlanDocument[]) {
    try {
      if (!this.expectedInitial) failure('本轮不可变配置预期未知；保留记录供独立审核');
      const version = checkpoint.session.sceneVersion;
      if (!version) failure('实际场景版本未知');
      const frame = await this.http<Snapshot>(`${this.route()}/snapshot`);
      if (!this.auditBefore) { this.auditBefore = frame; return '全部分段步骤已完成；正在采集两个实际仿真时刻的末态'; }
      if (frame.simTime - this.auditBefore.simTime < .1) return '末态采样等待仿真时间推进；尚未宣称实验成功';
      const detail = await this.http<{ root: string; sceneId: string; rev: number; contentHash: string }>(`/library/scenes/${encodeURIComponent(version)}`);
      if (checkpoint.session.sceneContentHash && detail.contentHash !== checkpoint.session.sceneContentHash) failure('运行场景与索引素材哈希不一致');
      const layout = await this.http(`/library/files/${encodeURIComponent(detail.root)}/scenes/${encodeURIComponent(detail.sceneId)}/${detail.rev}/mechanical-layout.json`);
      const cycles = await collectCycleEvidence(path => this.http(path), this.route(), plans, object(object(layout)?.bodies) ?? {}, checkpoint.session, this.stageStates);
      const manifest = await this.http<SceneManifest>(`${this.route()}/scene`);
      const state = await this.http<{ entities: Record<string, EntityValues> }>(`${this.route()}/state?keys=`);
      const final = await this.inspect(checkpoint.plan.id);
      if (this.checkpointToken(final) !== this.checkpointToken(checkpoint)) failure('末态采样期间控制、计划或物料发生变化');
      this.finalAudit = auditFinal(this.expectedInitial.material, final.entity.values.materialSnapshot, layout, manifest, this.auditBefore, frame, state.entities, cycles);
      if (this.initialComparison?.length) { this.finalAudit.errors.push(...this.initialComparison); this.finalAudit.material = false; this.finalAudit.status = 'invalid'; }
    } catch (error) {
      this.finalAudit = { status: 'unavailable', material: false, spatial: false, environment: false, cycles: false, experimentQualified: false,
        errors: [error instanceof Error ? error.message : String(error)] };
    }
    this.stop();
    return this.finalAudit.status === 'observations_valid' ? '本轮全部分段及末态观测核验通过；运输与完整录制证据另行核验' : '本轮分段已执行；末态核验未通过或证据缺失，保留现场与记录';
  }
  async ordinaryReplacement(planId: string, step: PlanStep): Promise<PlanStep> {
    if (!('do' in step)) return step;
    if (step.do.action !== 'wait') {
      const condition = step.expect ? { ...step.expect, entity: step.expect.entity ?? step.do.entity } :
        step.do.action === 'start' && this.flow?.catalog.entities[step.do.entity]?.type === 'passage-centrifuge/v1' ?
          { entity: step.do.entity, property: 'completedCycle', equals: step.do.params.operationId } : undefined;
      if (condition && typeof condition.property === 'string') {
        const operator = Object.keys(condition).find(key => ['equals', 'notEquals', 'lt', 'lte', 'gt', 'gte', 'in', 'notIn', 'isNull', 'notNull', 'stale', 'deviatesFrom'].includes(key));
        if (operator) {
          const result = await this.http<{ satisfied: boolean }>(`${this.route()}/check`, post({ key: condition.entity, property: condition.property,
            op: operator, value: (condition as Record<string, JsonValue>)[operator], ...('by' in condition ? { by: condition.by } : {}) }));
          if (result.satisfied) return { id: step.id, status: 'pending', check: condition };
        }
      }
      return step;
    }
    const { plan } = await this.inspect(planId);
    const result = plan.stepResults[String(step.id)]; const id = result?.invocationId ?? result?.invocationIds?.at(-1);
    if (!id && step.status === 'skipped') return step;
    if (!id) failure('等待的原调用身份未知，不能重算剩余教学时间');
    const invocation = await this.http<Invocation>(`${this.route()}/invocations/${encodeURIComponent(id)}`);
    if (!terminal(invocation.status) || typeof invocation.startedSimTime !== 'number' || typeof invocation.endedSimTime !== 'number') failure('等待的实际仿真计时未知');
    const seconds = Number(step.do.params.seconds) - (invocation.endedSimTime - invocation.startedSimTime);
    return { ...step, do: { ...step.do, params: { ...step.do.params, seconds: Math.max(0, seconds) } } };
  }
  async begin(planId: string, catalog: Catalog, mode: 'auto' | 'manual', single = false) {
    this.stop(); this.flow = undefined;
    const checkpoint = await this.inspect(planId); this.valid(checkpoint);
    if (catalog.sceneRevision !== checkpoint.session.sceneRevision) failure('Catalog 与当前场景版本不一致，禁止续行');
    const group = planId.startsWith('cp-') ? passageRound(planId, (await this.http<{ plans: PlanDocument[] }>(`/plans?sessionId=${encodeURIComponent(this.sessionId)}`)).plans) : undefined;
    const selected = group?.next?.id ?? planId;
    const current = selected === planId ? checkpoint : await this.inspect(selected);
    const fresh = (group?.parts ?? [current.plan]).every(part => part.steps.every(step => step.status === 'pending' && !part.stepResults[String(step.id)]?.invocationId && !part.stepResults[String(step.id)]?.invocationIds?.length)) &&
      Number(object(current.entity.values.materialSnapshot)?.operationCount ?? -1) === 0;
    const prepared = this.reviewedCheckpoints.get(selected)?.token === this.checkpointToken(current) && current.entity.values.controlOwner === this.owner;
    this.flow = { phase: 'acquiring', planId: selected, mode, single, catalog, needsReview: !fresh && !prepared, beganSimTime: current.session.simTime };
    return this.progress();
  }
  async progress(): Promise<string> {
    if (this.flowBusy) return '正在等待已提交请求的结果；不会并行派发';
    this.flowBusy = true;
    try { return await this.progressFlow(this.flow); } finally { this.flowBusy = false; }
  }
  private async progressFlow(flow: PassageFlow | undefined): Promise<string> {
    if (!flow) return '尚未开始';
    if (flow.phase === 'running') {
      const status = await this.advance(flow.planId);
      if (flow !== this.flow) { this.stop(); return '控制意图已更新，先前流程已停止派发'; }
      flow.planId = this.round?.current ?? flow.planId;
      if (flow.single && !this.observing) { this.stop(); flow.phase = 'ready'; }
      if (!this.dispatching && !this.observing) flow.phase = 'ready';
      if (!this.dispatching && this.active?.terminalFailure) { flow.needsReview = true; flow.phase = 'blocked'; }
      return status;
    }
    if (flow.phase === 'paused') return '仿真已暂停；已停止自动派发，点击继续完成停稳与恢复';
    if (flow.phase === 'review') return '请核对现场与修订差异，再点击继续';
    if (flow.phase === 'preparing') return '正在发布已核对的分段修订，尚未派发设备动作';
    if (flow.phase === 'manual') return '人工接管已完成；持物与液体保留，自动派发已停止';
    if (flow.phase === 'ready') return '当前操作完成，可单步、运行或接管';
    if (flow.phase === 'blocked') return '操作受阻；保留现场，查看详情后继续核对';
    const checkpoint = await this.inspect(flow.planId); const values = this.valid(checkpoint);
    if (flow !== this.flow) return '控制意图已更新，先前流程已停止派发';
    if (checkpoint.session.simTime - flow.beganSimTime > 120) { this.block(); return '停稳与交接超出仿真时间预算，保留现场后重新核对'; }
    if (values.controlOwner !== this.owner || values.controlMode !== flow.mode) {
      if (values.controlMode !== 'holding') {
        await this.requestControl(flow.planId, flow.mode);
        return '正在停止先前控制并建立保持目标；尚未确认停稳';
      }
      await this.cancelActive(flow.planId);
      if (flow !== this.flow) return '控制意图已更新，先前流程已停止派发';
      if (values.controlSafe !== true || (await this.pendingControlled()).length) {
        if (checkpoint.session.state === 'PAUSED') { flow.phase = 'paused-hold'; return '仿真暂停中，保持请求已生效；点击继续推进物理制动，自动派发仍停止'; }
        return '正在观察物理停稳与在途调用终态；保留持物及液体';
      }
      await this.confirmControl(flow.planId);
      return '源侧已确认交接，正在核对当前现场';
    }
    if (values.controlSafe !== true) return '正在等待源侧实测停稳';
    if (flow.mode === 'manual' && !flow.single) { flow.phase = 'manual'; return '人工接管已完成；自动派发已停止'; }
    const prepared = this.reviewedCheckpoints.get(flow.planId)?.token === this.checkpointToken(checkpoint);
    if (prepared && !flow.needsReview && !checkpoint.plan.steps.some(step => ['failed', 'skipped', 'running'].includes(step.status))) {
      if (checkpoint.session.state === 'PAUSED') await this.resume();
      await this.arm(flow.planId, flow.mode);
      if (flow !== this.flow) { this.stop(); return '控制意图已更新，先前流程已停止派发'; }
      flow.phase = 'running'; return '已核对连续上下文，正在执行';
    }
    flow.review = await this.review(flow.planId, flow.catalog);
    if (flow !== this.flow) return '控制意图已更新，先前流程已停止派发';
    if (flow.needsReview || checkpoint.plan.steps.some(step => ['failed', 'skipped'].includes(step.status))) {
      flow.phase = 'review'; return '现场核对已准备；仅修订实际受影响步骤，液体操作须明确余量';
    }
    await this.prepareRemaining(flow.planId, flow.review);
    if (flow !== this.flow) return '控制意图已更新，先前流程已停止派发';
    if (checkpoint.session.state === 'PAUSED') await this.resume();
    await this.arm(flow.planId, flow.mode);
    if (flow !== this.flow) { this.stop(); return '控制意图已更新，先前流程已停止派发'; }
    flow.phase = 'running'; return '就绪检查与控制交接已完成，开始执行';
  }
  async continueFlow(replacements: Record<number, PlanStep> = {}, reviewed = false) {
    const flow = this.flow;
    if (!flow) failure('先选择本轮计划');
    if (flow.mode === 'manual' && !flow.single && !['paused-hold', 'paused'].includes(flow.phase))
      return this.begin(flow.planId, flow.catalog, 'auto');
    if (flow.phase === 'paused-hold' || flow.phase === 'paused') {
      await this.resume(); flow.beganSimTime = (await this.inspect(flow.planId)).session.simTime;
      flow.phase = 'acquiring'; return '仿真已恢复以完成保持与核对；自动派发尚未恢复';
    }
    if (flow.phase === 'review') {
      if (!reviewed || !flow.review) failure('请核对现场与实际修订差异');
      flow.phase = 'preparing';
      await this.prepareRemaining(flow.planId, flow.review, replacements);
      if (flow !== this.flow) return '控制意图已更新，先前流程已停止派发';
      const checkpoint = await this.inspect(flow.planId);
      if (checkpoint.session.state === 'PAUSED') await this.resume();
      await this.arm(flow.planId, flow.mode);
      if (flow !== this.flow) { this.stop(); return '控制意图已更新，先前流程已停止派发'; }
      flow.phase = 'running'; flow.needsReview = false;
      return '核对与修订通过，继续当前轮次';
    }
    flow.beganSimTime = (await this.inspect(flow.planId)).session.simTime;
    flow.phase = 'acquiring'; return this.progress();
  }
  async receiptFor(planId: string, step: PlanStep) {
    if (!('do' in step)) return undefined;
    const operationId = step.do.params.operationId;
    const valid = (value: unknown) => {
      const row = object(value);
      return row && row.operationId === operationId && ['succeeded', 'failed', 'cancelled'].includes(String(row.status)) &&
        ['appliedUl', ...(passageContract.liquidActions.quantityIntent.includes(step.do.action) ? ['resolvedUl', 'remainingUl'] : [])]
          .every(key => typeof row[key] === 'number' && Number.isFinite(row[key]) && Number(row[key]) >= 0) ? row : undefined;
    };
    const { entity, plan } = await this.inspect(planId);
    const current = valid(operationReceipt(step, entity.values.materialSnapshot));
    if (current) return current;
    const result = plan.stepResults[String(step.id)];
    const ids = result?.invocationIds ?? (result?.invocationId ? [result.invocationId] : []);
    for (const id of [...ids].reverse()) {
      const invocation = await this.http<Invocation>(`${this.route()}/invocations/${encodeURIComponent(id)}`);
      const receipt = valid(object(invocation.result)?.receipt) ?? valid(invocation.result);
      if (receipt) return receipt;
    }
    // The event stream may have a history gap. An unavailable receipt is never reconstructed from current stock.
    const history = await this.http<{ events: { message: { type: string; entityKey?: string; event?: string; data?: JsonValue } }[] }>(`${this.route()}/events?after=0&timeoutSeconds=0`);
    for (const event of [...history.events].reverse()) {
      if (event.message.entityKey === this.workcell && event.message.event === 'operationCompleted') {
        const receipt = valid(object(event.message.data)?.receipt); if (receipt) return receipt;
      }
    }
    return undefined;
  }
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
    if (session.sessionId !== this.sessionId || plan.sessionId !== this.sessionId || plan.sceneRevision !== session.sceneRevision || plan.epoch !== session.epoch) failure('会话已改变；请另开一轮');
    if (!material.reconciled || material.stale || entity.values.auditPassed !== true) failure('物料未知、过期或未通过核对，禁止派发');
    if (['controlOwner', 'controlEpoch', 'controlMode', 'controlSafe', 'materialRevision', 'auditPassed'].some(key => entity.meta[key]?.stale === true)) failure('控制或物料读数已过期');
    if (typeof entity.values.controlOwner !== 'string' || !Number.isInteger(entity.values.controlEpoch) || !Number.isInteger(entity.values.materialRevision)) failure('控制或物料修订未知');
    if (material.snapshot?.revision !== entity.values.materialRevision) failure('物料快照与公开修订不一致，禁止派发');
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
      if (!terminal(invocation.status) && !this.cancellationRequests.has(id)) {
        this.cancellationRequests.add(id);
        await this.http(`${this.route()}/invocations/${encodeURIComponent(id)}/cancel`, post({}));
      }
    }
  }
  private checkpointToken(checkpoint: Checkpoint) {
    const { session, plan, entity } = checkpoint;
    return JSON.stringify([session.sessionId, session.sceneRevision, session.epoch, plan.id, plan.revision,
      entity.values.controlOwner, entity.values.controlEpoch, entity.values.controlMode,
      entity.values.materialRevision]);
  }
  async review(planId: string, catalog: Catalog): Promise<Review> {
    this.stop();
    const checkpoint = await this.inspect(planId);
    if (catalog.sceneRevision !== checkpoint.session.sceneRevision) failure('Catalog 与当前场景版本不一致，禁止续行');
    const values = this.valid(checkpoint);
    const expected = configurationInitial(catalog, this.workcell, checkpoint.session.sceneRevision);
    if (this.expectedInitial && this.expectedInitial.fingerprint !== expected.fingerprint) failure('同一场景的固定配方配置发生变化，请另开一轮核对');
    this.expectedInitial = expected;
    const conservation = auditConservation(expected.material, values.materialSnapshot);
    if (conservation.length) failure(`当前物料与配置预期守恒不符：${conservation.join('；')}`);
    if (values.controlOwner !== this.owner || !['auto', 'manual'].includes(String(values.controlMode)) || values.controlSafe !== true) failure('先完成浏览器控制交接并停稳');
    const binding = catalog.entities[this.workcell];
    if (!binding || !Array.isArray(binding.config.managedDevices) || !Array.isArray(binding.config.vessels)) failure('机械核对配置缺失');
    const managed = [this.workcell, ...binding.config.managedDevices.map(String)];
    if (managed.some(key => !catalog.entities[key])) failure('受管设备核对范围不完整');
    const vessels = binding.config.vessels.map(object);
    if (vessels.some(row => !row || typeof row.entityKey !== 'string')) failure('器皿核对范围不完整');
    const staticVessels = vessels.filter(row => !catalog.entities[String(row!.entityKey)]);
    const staticPoses: NonNullable<Review['staticPoses']> = [];
    if (staticVessels.length) {
      const manifest = await this.http<SceneManifest>(`${this.route()}/scene`);
      for (const vessel of staticVessels) {
        const key = String(vessel!.entityKey), site = String(vessel!.site);
        if (vessel!.lidProperty || vessel!.requiredStation || !manifest.nodes.some(node => node.entityKey === key && ['body', 'static'].includes(node.kind)) ||
            !manifest.sites.some(row => row.entityKey === site && row.parentBodyKey === key)) failure(`静态器皿缺少可信物理 body/Site：${key}`);
        const pose = await this.http<PublicPose>(`${this.route()}/pose?key=${encodeURIComponent(key)}`);
        if (pose.sceneRevision !== checkpoint.session.sceneRevision || pose.epoch !== checkpoint.session.epoch ||
            pose.p.length !== 3 || pose.q.length !== 4 || ![...pose.p, ...pose.q].every(Number.isFinite)) failure(`静态器皿物理位姿未知：${key}`);
        staticPoses.push({ entity: key, site, pose });
      }
    }
    const keys = [...new Set([...managed, ...vessels.map(row => String(row!.entityKey)).filter(key => !!catalog.entities[key])])];
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
        const transient = ['temperatureC', 'co2Percent', 'humidityPercent', 'inflowMs', 'sashHeightCm',
          'jointPositions', 'tcpPose', 'gripperWidth', 'position', 'velocity', 'rpm', 'actualRpm', 'phase', 'waitElapsedSeconds'];
        facts.push({ entity: key, property, value: entity.values[property], verify: !transient.includes(property) });
      }
    }
    const token = this.checkpointToken(checkpoint);
    if (this.checkpointToken(await this.inspect(planId)) !== token) failure('核对期间现场已变化，请重新检查');
    return { token, facts, staticPoses, stamp: { materialRevision: Number(values.materialRevision), controlEpoch: Number(values.controlEpoch), planRevision: checkpoint.plan.revision } };
  }
  async prepareRemaining(planId: string, review: Review, replacements: Record<number, PlanStep> = {}) {
    this.stop();
    if (this.revisionUncertain) failure('计划修订结果未知；只查询已知计划，禁止重新提交修订');
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
    for (const fact of review.facts.filter(fact => fact.verify !== false)) {
      const result = await this.http<{ satisfied: boolean }>(`${this.route()}/check`, post({ key: fact.entity,
        property: fact.property, op: 'equals', value: fact.value }));
      if (!result.satisfied) failure('核对事实已变化，禁止修订续行');
    }
    for (const item of review.staticPoses ?? []) {
      const pose = await this.http<PublicPose>(`${this.route()}/pose?key=${encodeURIComponent(item.entity)}`);
      const distance = Math.hypot(...pose.p.map((value, index) => value - item.pose.p[index]));
      const dot = Math.abs(pose.q.reduce((sum, value, index) => sum + value * item.pose.q[index], 0));
      const angle = 2 * Math.acos(Math.min(1, dot)) * 180 / Math.PI;
      if (pose.epoch !== item.pose.epoch || pose.sceneRevision !== item.pose.sceneRevision ||
          !Number.isFinite(distance + angle) || distance >= passageContract.mechanicalTolerances.positionM || angle > passageContract.mechanicalTolerances.orientationDegrees)
        failure('静态器皿位姿发生有意义变化，重新核对');
    }
    if (this.checkpointToken(await this.inspect(planId)) !== review.token) failure('核对期间现场已变化，禁止续行');
    const listed = roundIdentity(planId) ? (await this.http<{ plans: PlanDocument[] }>(`/plans?sessionId=${encodeURIComponent(this.sessionId)}`)).plans : [checkpoint.plan];
    const group = passageRound(planId, listed);
    if (group && group.next?.id !== planId) failure('请先选择本轮最早未完成分段');
    const plans = group?.parts ?? [checkpoint.plan];
    if (plans.some(plan => plan.sessionId !== this.sessionId || plan.sceneRevision !== checkpoint.session.sceneRevision || plan.epoch !== checkpoint.session.epoch)) failure('本轮分段会话已改变');
    const plan = checkpoint.plan;
    for (const step of plan.steps) if (step.status === 'failed' && 'do' in step && liquidAction(step.do.action) && !(await this.receiptFor(planId, step)))
      failure('原操作的可靠 receipt 不可用；保留现场并核对记录，禁止按当前余量重新计算');
    const documents = revisePlans(plans, this.owner, Number(values.controlEpoch), { [plan.id]: replacements });
    if (!this.initialMaterial && plans.every(part => part.steps.every(step => step.status === 'pending')) && object(values.materialSnapshot)?.operationCount === 0) {
      this.initialMaterial = structuredClone(values.materialSnapshot);
      if (this.expectedInitial) this.initialComparison = compareFreshInitial(this.expectedInitial, this.initialMaterial);
      if (this.initialComparison?.length) failure(`fresh 初态不符合配置预期：${this.initialComparison.join('；')}`);
    }
    const revisions = new Map(plans.map(part => [part.id, part.revision]));
    let selected = plan;
    for (const [id, document] of documents) {
      this.revisionUncertain = true;
      const revised = await this.http<PlanDocument>(`/plans/${encodeURIComponent(id)}/revisions`, post({ ...document, kind: 'operation', status: 'draft' }));
      this.revisionUncertain = false;
      const current = await this.inspect(id);
      if (current.plan.revision !== revised.revision || current.entity.values.materialRevision !== values.materialRevision ||
          current.entity.values.controlOwner !== this.owner || current.entity.values.controlEpoch !== values.controlEpoch) failure('修订后现场变化，请重新核对');
      this.reviewedCheckpoints.set(id, { revision: current.plan.revision, token: this.checkpointToken(current) });
      revisions.set(id, revised.revision);
      if (id === planId) selected = revised;
    }
    this.round = group ? { current: planId, revisions } : undefined;
    return selected;
  }
  async pause() {
    this.stop();
    if (this.flow) this.flow = { ...this.flow, phase: 'paused', needsReview: true };
    const result = await this.http(`${this.route()}/commands`, post({ requestId: crypto.randomUUID(), command: 'pause' }));
    const state = await this.http<{ entities: Record<string, EntityValues> }>(`${this.route()}/state?keys=${encodeURIComponent(this.workcell)}`);
    const values = state.entities[this.workcell]?.values;
    if (values?.controlMode !== 'holding' && typeof values?.controlOwner === 'string' && Number.isInteger(values.controlEpoch))
      await this.http(`${this.route()}/actions`, post({ requestId: crypto.randomUUID(), entityKey: this.workcell, action: 'requestControl',
        params: { owner: values.controlOwner, epoch: values.controlEpoch, operationId: crypto.randomUUID(), nextOwner: this.owner, mode: this.flow?.mode ?? 'manual' } }));
    return result;
  }
  async resume() {
    return this.http(`${this.route()}/commands`, post({ requestId: crypto.randomUUID(), command: 'resume' }));
  }
  async arm(planId: string, mode: 'auto' | 'manual' = 'auto') {
    const checkpoint = await this.inspect(planId);
    const values = this.valid(checkpoint);
    if (values.controlOwner !== this.owner || values.controlMode !== mode || values.controlSafe !== true) failure('先取得相应浏览器控制并等待停稳');
    if (this.round && this.round.current !== planId) failure('本轮必须按分段顺序执行');
    if (this.reviewedCheckpoints.get(planId)?.token !== this.checkpointToken(checkpoint)) failure('先读取现场核对并准备后续步骤');
    if (checkpoint.plan.steps.some(step => ['running', 'failed'].includes(step.status)) || this.active) failure('先观察或取消在途调用，并修订失败步骤');
    if (typeof values.materialRevision !== 'number' || typeof values.controlEpoch !== 'number') failure('修订状态未知');
    this.pinned = { sceneRevision: checkpoint.session.sceneRevision, epoch: checkpoint.session.epoch, revision: checkpoint.plan.revision,
      controlEpoch: values.controlEpoch, controlMode: mode, materialRevision: values.materialRevision };
    this.stopped = false;
  }
  async advance(planId: string) {
    if (this.busy) return '正在处理上一请求';
    this.busy = true;
    try { return await this.advanceOnce(this.round?.current ?? planId); } finally { this.busy = false; }
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
        this.active.terminalFailure = true; this.stop(); return '调用未成功或计划期望未通过；保留现场并修订后续步骤';
      }
      const uncertainStopGeneration = this.active.uncertainStopGeneration;
      this.active = undefined;
      const after = await this.inspect(planId);
      if (after.entity.values.controlOwner !== this.owner || (this.pinned && after.entity.values.controlEpoch !== this.pinned.controlEpoch)) this.stop();
      else if (uncertainStopGeneration === this.stopGeneration && this.flow?.phase === 'running' && !this.flow.single && after.plan.revision === this.pinned?.revision) this.stopped = false;
      if (this.pinned) this.pinned.materialRevision = Number(after.entity.values.materialRevision);
      for (const [id, prepared] of this.reviewedCheckpoints) prepared.token = this.checkpointToken({ ...after, plan: { ...after.plan, id, revision: prepared.revision } });
      return '当前步骤已完成';
    }
    if (this.stopped || !this.pinned) return '已停止派发；核对后继续';
    const pinned = this.pinned;
    if (session.sceneRevision !== pinned.sceneRevision || session.epoch !== pinned.epoch || plan.revision !== pinned.revision ||
        values.controlOwner !== this.owner || values.controlEpoch !== pinned.controlEpoch || values.controlMode !== pinned.controlMode ||
        values.materialRevision !== pinned.materialRevision) { this.stop(); return '控制、物料或计划已改变；停止派发，必须重新核对'; }
    if (session.state === 'PAUSED') return '仿真暂停；等待与机械动作均冻结';
    if (session.state !== 'RUNNING') { this.stop(); return '会话已中断；不能作为执行检查点'; }
    if (values.controlSafe !== true) {
      this.settlingSince ??= session.simTime;
      if (session.simTime - this.settlingSince > 120) { this.stop(); return '设备停稳超出仿真时间预算；保留现场并核对'; }
      return '设备正在停稳；继续观察源侧安全状态';
    }
    this.settlingSince = undefined;
    if (plan.status === 'completed') {
      if (this.round) {
        const listed = await this.http<{ plans: PlanDocument[] }>(`/plans?sessionId=${encodeURIComponent(this.sessionId)}`);
        const group = passageRound(planId, listed.plans);
        if (!group || group.parts.some(part => this.round!.revisions.get(part.id) !== part.revision)) { this.stop(); return '本轮分段已改变，必须重新核对'; }
        const identity = roundIdentity(planId)!;
        if (identity.part === identity.parts && !this.stageStates.has(identity.stage)) this.stageStates.set(identity.stage, (await this.http<{ entities: Record<string, EntityValues> }>(`${this.route()}/state?keys=`)).entities);
        if (group.next) {
          this.round.current = group.next.id;
          await this.arm(group.next.id, this.pinned?.controlMode === 'manual' ? 'manual' : 'auto');
          return '当前分段完成；已核对下一分段，持物与物料保留';
        }
        return this.captureFinal(checkpoint, group.parts);
      }
      this.stop(); return '当前计划完成；整轮传代仍待全部阶段与结果核验';
    }
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
      this.active.uncertainStopGeneration = this.stopGeneration;
      return '请求响应丢失；查询已知计划调用，禁止重放';
    }
  }
}
