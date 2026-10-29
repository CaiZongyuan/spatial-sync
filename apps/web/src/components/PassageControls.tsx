import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { JsonValue, OperationPlan } from '../lib/protocol';
import type { SessionClient } from '../lib/stream/client';
import type { PlanStep } from '../lib/plans/model';
import { factLabel, object, quantity, rows, text } from '../lib/cell-passage/model';
import { useEntity } from '../lib/info/hooks';
import { PassageController, type Review } from '../lib/cell-passage/controller';
import { liquidAction, literalRemainder } from '../lib/cell-passage/recovery';
import { roundIdentity } from '../lib/cell-passage/round';

export function PassageControls({ client, workcell, plan, blocked, selectPlan }: {
  client: SessionClient; workcell: string; plan?: OperationPlan; blocked?: string; selectPlan?: (id: string) => void;
}) {
  const material = useEntity(client.info, workcell);
  const [replacements, setReplacements] = useState<Record<number, { approved: boolean; volume: string; direction?: 'aspirate' | 'dispense'; checkOnly?: boolean }>>({});
  const [receipts, setReceipts] = useState<Record<number, Record<string, JsonValue>>>({});
  const [receiptError, setReceiptError] = useState('');
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const group = roundIdentity(plan?.id ?? '')?.roundId ?? plan?.id;
  const controller = useMemo(() => new PassageController(client.sessionId, workcell), [client, workcell, group]);
  const metadata = useSyncExternalStore(client.info.subscribeMetadata, client.info.getMetadata);
  const [review, setReview] = useState<Review>();
  const [reviewed, setReviewed] = useState(false);
  const [message, setMessage] = useState('选择运行或单步；控制交接与就绪检查由客户端完成。');
  const [pending, setPending] = useState(false);
  const [polling, setPolling] = useState(false);
  const alive = useRef(true);
  const accessBlocked = client.readOnly ? '回放不可执行' : view.role !== 'operator' ? '需要操作者权限' :
    view.connection !== 'connected' ? '连接不可用' : view.evaluationId ? '评估会话禁止人工控制' : !plan ? '先选择操作计划' : undefined;
  const disabled = blocked ?? accessBlocked;
  const controlMode = material?.values.controlMode;
  const controlSafe = material?.values.controlSafe === true;
  useEffect(() => {
    let cancelled = false; setReceipts({}); setReceiptError('');
    if (plan) void Promise.all(plan.steps.filter(step => step.status === 'failed' && 'do' in step && liquidAction(step.do.action)).map(async step => {
      const receipt = await controller.receiptFor(plan.id, step);
      if (!receipt) throw new Error('原操作 receipt 未找到，请保留现场并核对录制；不能重新计算数量意图');
      return [step.id, receipt] as const;
    })).then(values => { if (!cancelled) setReceipts(Object.fromEntries(values)); }).catch(error => { if (!cancelled) setReceiptError(String(error.message ?? error)); });
    return () => { cancelled = true; };
  }, [controller, plan?.revision, material?.values.materialRevision]);
  useEffect(() => {
    setReview(undefined); setReviewed(false); setReplacements({});
    const stamp = controller.flow?.review?.stamp;
    if (controller.flow?.phase === 'review' && stamp && (Number(material?.values.materialRevision) > stamp.materialRevision || Number(material?.values.controlEpoch) > stamp.controlEpoch || Number(plan?.revision) > stamp.planRevision)) {
      controller.block(); setPolling(false); setMessage('物料、控制或计划发生变化；原核对失效，请重新继续核对');
    }
  }, [controller, material?.values.controlEpoch, material?.values.materialRevision, plan?.revision]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; controller.stop(); }; }, [controller]);
  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (cancelled || !plan) return;
      try {
        const status = controller.flow ? await controller.progress() : await controller.advance(plan.id);
        if (cancelled) return;
        setMessage(status);
        if (controller.currentPlanId && controller.currentPlanId !== plan.id) selectPlan?.(controller.currentPlanId);
        setPolling(controller.polling);
      } catch (error) {
        controller.block(); setPolling(false); setMessage(String(error instanceof Error ? error.message : error));
      }
      if (!cancelled) timer = setTimeout(poll, 500);
    };
    timer = setTimeout(poll, 500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [polling, controller, plan?.id]);
  useEffect(() => { if (disabled) { controller.block(); setPolling(false); } }, [disabled, controller]);
  const revisedFailures = async () => {
    const result: Record<number, PlanStep> = {};
    for (const step of plan?.steps.filter(step => ['failed', 'skipped'].includes(step.status)) ?? []) {
      const input = replacements[step.id];
      const isLiquid = 'do' in step && liquidAction(step.do.action);
      if (isLiquid && !input?.approved) throw new Error(`先明确修订失败步骤 ${step.id}`);
      if (!isLiquid) { result[step.id] = await controller.ordinaryReplacement(plan!.id, step); continue; }
      if (input?.checkOnly) {
        if (step.status === 'failed' && !receipts[step.id]) throw new Error('原始操作 receipt 未知，禁止续行');
        const revision = controller.flow?.review?.stamp?.materialRevision ?? material?.values.materialRevision;
        if (!Number.isInteger(revision)) throw new Error('当前物料修订未知');
        result[step.id] = { id: step.id, status: 'pending', check: { entity: workcell, property: 'materialRevision', equals: revision! } }; continue;
      }
      if ('do' in step) {
        const liquid = liquidAction(step.do.action);
        const volume = Number(input?.volume);
        if (liquid && (!input?.volume || !Number.isFinite(volume) || volume <= 0)) throw new Error('根据已应用量与当前液体填写剩余操作量');
        if (liquid && step.status === 'failed' && !receipts[step.id]) throw new Error('原始操作 receipt 未知，禁止续行');
        result[step.id] = liquid ? literalRemainder(step, volume, input?.direction) : step;
      }
    }
    return result;
  };
  const run = async (action: () => Promise<unknown>, success?: string, safeStop = false) => {
    if (pending || (safeStop ? accessBlocked : disabled)) return;
    setPending(true);
    try {
      const result = await action();
      if (alive.current) {
        setMessage(typeof result === 'string' ? result : success ?? '操作已受理，正在观察源侧状态'); setPolling(controller.polling);
        if (controller.currentPlanId && controller.currentPlanId !== plan?.id) selectPlan?.(controller.currentPlanId);
      }
      else controller.block();
    }
    catch (error) { controller.block(); setPolling(false); if (alive.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (alive.current) setPending(false); }
  };
  const start = (auto: boolean) => run(() => controller.begin(plan!.id, metadata.catalog!, !auto && controlMode === 'manual' ? 'manual' : 'auto', !auto));
  const currentReview = controller.flow?.review ?? review;
  return <section className="passage-controls" aria-label="传代控制">
    <h3>运行与交接</h3><p role="status">{message}</p>
    <div className="passage-buttons" aria-label="主要传代操作">
      <button disabled={!!disabled || pending || polling || !metadata.catalog} onClick={() => void start(true)}>运行</button>
      <button disabled={!!disabled || pending || polling || !metadata.catalog} onClick={() => void start(false)}>单步</button>
      <button disabled={!!accessBlocked || pending} onClick={() => void run(() => controller.pause(), '仿真已暂停；保持请求已建立，尚未宣称停稳', true)}>暂停</button>
      <button disabled={!!disabled || pending || !metadata.catalog} onClick={() => void run(() => controller.begin(plan!.id, metadata.catalog!, 'manual'))}>接管</button>
      <button disabled={!!disabled || pending || !metadata.catalog} onClick={() => void run(async () => {
        if (!controller.flow || ['manual', 'ready'].includes(controller.flow.phase)) return controller.begin(plan!.id, metadata.catalog!, 'auto');
        return controller.continueFlow(controller.flow.phase === 'review' ? await revisedFailures() : {}, controller.flow.phase === 'review');
      })}>继续</button>
    </div>
    {disabled && <p>{disabled}</p>}
    {receiptError && <p role="alert">{receiptError}</p>}
    {controller.finalAudit && <section aria-label="本轮末态核验"><h4>末态核验：{controller.finalAudit.status === 'observations_valid' ? '观测检查通过' : '未通过或证据不足'}</h4><ul><li>物料守恒与十二子孔：{controller.finalAudit.material ? '通过' : '未通过/未知'}</li><li>实际就座与废吸头：{controller.finalAudit.spatial ? '通过' : '未通过/未知'}</li><li>教学环境：{controller.finalAudit.environment ? '通过' : '未通过/未知'}</li></ul><p>完整实验验收仍需连续录制与运输证据；单个分段完成不代表实验成功。</p>{controller.finalAudit.errors.length > 0 && <details><summary>核验缺项与差异</summary><ul>{controller.finalAudit.errors.map((error, index) => <li key={index}>{error}</li>)}</ul></details>}</section>}
    {controlMode === 'holding' && <p>已禁止先前控制继续操作；{controlSafe ? '源侧工作单元已停稳，确认仍会核验所有受管设备' : '正在建立物理保持目标并等待实测停稳，尚不可确认交接'}。</p>}
    {controller.flow?.phase === 'review' && currentReview && <div className="passage-review"><h4>续行前核对</h4><p>确认当前持物、在途液体、盖门与占用后点击继续。普通等待按已发生仿真时间续算，搬运沿原声明目标重新核验；液体失败须在下方明确修订剩量。</p><ul>{currentReview.facts.filter(fact => ['holding', 'station', 'lidClosed', 'doorOpen', 'slots', 'inFlightUl', 'controlSafe', 'environmentReady', 'cabinetReady'].includes(fact.property)).map(fact => <li key={`${fact.entity}:${fact.property}`}>{fact.entity.split('/').at(-1)} · {fact.property}：{factLabel(fact.value)}</li>)}</ul></div>}
    {plan?.steps.filter(step => ['failed', 'skipped'].includes(step.status) && 'do' in step && liquidAction(step.do.action)).map(step => {
      const operation = receipts[step.id];
      const liquid = 'do' in step && liquidAction(step.do.action);
      const snapshot = object(material?.values.materialSnapshot);
      const relevant = 'do' in step ? rows(snapshot?.vessels).filter(row => row.id === step.do.params.vessel || row.id === step.do.params.targetVessel) : [];
      return <fieldset key={step.id}><legend>修订失败步骤 {step.id} · {'do' in step ? step.do.action : '检查'}</legend>
        {liquid && <><p>原解析目标 {quantity(operation?.resolvedUl)} µL；已实转 {quantity(operation?.appliedUl)} µL；解析目标剩余 {quantity(operation?.remainingUl)} µL；分辨率余量 {quantity(operation?.resolutionResidualUl)} µL。以原 receipt 与当前器皿/吸头内容修订明确操作，不重新计算 fraction、top-up 或 all。</p>
          <label><input type="checkbox" checked={replacements[step.id]?.checkOnly ?? false} onChange={event => setReplacements(previous => ({ ...previous, [step.id]: { ...previous[step.id], checkOnly: event.target.checked, approved: false } }))} /> 改为仅核对当前物料快照，不再移液（明确变更本步骤意图）</label>
          <table aria-label={`步骤 ${step.id} 当前物料`}><thead><tr><th>当前位置</th><th>实际液量 µL</th></tr></thead><tbody>{relevant.map(row => <tr key={text(row.id)}><td>{text(row.id)}</td><td>{quantity(row.volumeUl)}</td></tr>)}<tr><td>已装吸头</td><td>{quantity(object(snapshot?.tip)?.volumeUl)}</td></tr></tbody></table>
          <details><summary>原操作与余量明细</summary><dl>{['requestedUl', 'resolvedUl', 'appliedUl', 'observedUl', 'remainingUl', 'untransferredUl', 'resolutionResidualUl', 'quantityOutcome', 'remainderLocation'].map(key => <div key={key}><dt>{key}</dt><dd>{factLabel(operation?.[key])}</dd></div>)}</dl></details>
          {liquid === 'mix' && <label>修复方向<select value={replacements[step.id]?.direction ?? ''} onChange={event => setReplacements(previous => ({ ...previous, [step.id]: { ...previous[step.id], direction: event.target.value as 'aspirate' | 'dispense', approved: false } }))}><option value="">根据当前吸头内容选择</option><option value="aspirate">吸液</option><option value="dispense">分液</option></select></label>}
          <label>后续操作量（µL）<input type="number" min="0" step="any" value={replacements[step.id]?.volume ?? ''} onChange={event => setReplacements(previous => ({ ...previous, [step.id]: { ...previous[step.id], volume: event.target.value, approved: false } }))} /></label></>}
        {liquid && typeof operation?.remainingUl === 'number' && operation.remainingUl > 0 && <button onClick={() => setReplacements(previous => ({ ...previous, [step.id]: { ...previous[step.id], volume: String(operation.remainingUl), approved: false } }))}>填入原解析目标剩量 {quantity(operation.remainingUl)} µL</button>}
        <label><input type="checkbox" disabled={!!liquid && !operation && step.status !== 'skipped'} checked={replacements[step.id]?.approved ?? false} onChange={event => setReplacements(previous => ({ ...previous, [step.id]: { ...previous[step.id], volume: previous[step.id]?.volume ?? '', approved: event.target.checked } }))} /> 已核对原目标、实际量和当前余量差异；采用上述明确修订，不重放原数量意图</label>
      </fieldset>;
    })}
    <details className="passage-advanced"><summary>高级现场与控制详情</summary>
      {currentReview && <div className="passage-review"><ul>{currentReview.facts.map(fact => <li key={`${fact.entity}:${fact.property}`}><code>{fact.entity}</code> · {fact.property}：{factLabel(fact.value)}</li>)}</ul><p>静态储液槽/废物容器：{currentReview.staticPoses?.length ?? 0} 个实际 body/Site 位姿已读取，不伪造 station。</p></div>}
      {review && <label><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} /> 已核对高级操作的现场快照</label>}
    <div className="passage-buttons">
      <button disabled={!!disabled || pending || controlMode === 'holding'} onClick={() => void run(() => controller.requestControl(plan!.id, 'auto'), '已请求浏览器控制；先前控制已失效，等待源侧停稳并核对现场')}>申请浏览器控制</button>
      <button disabled={!!disabled || pending} onClick={() => void run(() => controller.cancelActive(plan!.id), '取消已受理/运行调用；等待物理保持与调用终态，保留持物及液体')}>取消在途调用</button>
      <button disabled={!!disabled || pending || controlMode !== 'holding' || !controlSafe} onClick={() => void run(() => controller.confirmControl(plan!.id), '源侧已确认控制交接；当前物料与持物均保留')}>确认停稳交接</button>
      <button disabled={!!disabled || pending || !metadata.catalog} onClick={() => void run(async () => { setReview(await controller.review(plan!.id, metadata.catalog!)); setReviewed(false); }, '请核对机械事实及物料，确认后再准备未开始步骤')}>读取现场核对</button>
      <button disabled={!!disabled || pending || !reviewed || !review} onClick={() => void run(async () => controller.prepareRemaining(plan!.id, review!, await revisedFailures()), '已修订未开始步骤的控制身份；完成步骤及结果保留')}>准备未开始步骤</button>
      <button disabled={!!disabled || pending || view.sessionState !== 'PAUSED'} onClick={() => void run(() => controller.resume(), '仿真已恢复；继续先前已受理动作，未自动重发')}>恢复仿真</button>
    </div>
    </details>
    <p>暂停冻结仿真时间。交接中的物理停稳需要仿真运行；恢复仿真后观察源侧状态。人工接管后须核对持物、吸头/在途液体、盖门、转子、占用与物料修订；失败或部分完成步骤不能直接重试。</p>
  </section>;
}
