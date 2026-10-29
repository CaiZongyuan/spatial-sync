import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { JsonValue, OperationPlan } from '../lib/protocol';
import type { SessionClient } from '../lib/stream/client';
import type { PlanStep } from '../lib/plans/model';
import { factLabel, object, quantity, rows, text } from '../lib/cell-passage/model';
import { useEntity } from '../lib/info/hooks';
import { PassageController, type Review } from '../lib/cell-passage/controller';
import { liquidAction, literalRemainder } from '../lib/cell-passage/recovery';

export function PassageControls({ client, workcell, plan, blocked }: {
  client: SessionClient; workcell: string; plan?: OperationPlan; blocked?: string;
}) {
  const material = useEntity(client.info, workcell);
  const [replacements, setReplacements] = useState<Record<number, { approved: boolean; volume: string; direction?: 'aspirate' | 'dispense' }>>({});
  const [receipts, setReceipts] = useState<Record<number, Record<string, JsonValue>>>({});
  const [receiptError, setReceiptError] = useState('');
  const view = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const controller = useMemo(() => new PassageController(client.sessionId, workcell), [client, workcell, plan?.id]);
  const metadata = useSyncExternalStore(client.info.subscribeMetadata, client.info.getMetadata);
  const [review, setReview] = useState<Review>();
  const [reviewed, setReviewed] = useState(false);
  const [message, setMessage] = useState('先申请控制，等待源侧停稳，再确认交接。');
  const [pending, setPending] = useState(false);
  const [automatic, setAutomatic] = useState(false);
  const [observe, setObserve] = useState(false);
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
  useEffect(() => { setReview(undefined); setReviewed(false); setReplacements({}); }, [controller, material?.values.controlEpoch, material?.values.materialRevision, plan?.revision]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; controller.stop(); }; }, [controller]);
  useEffect(() => {
    if (!automatic && !observe) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (cancelled || !plan) return;
      try {
        const status = await controller.advance(plan.id);
        if (cancelled) return;
        setMessage(status);
        setAutomatic(current => current && controller.dispatching);
        setObserve(controller.observing);

      } catch (error) {
        controller.stop(); setAutomatic(false); setMessage(String(error instanceof Error ? error.message : error));
      }
      if (!cancelled) timer = setTimeout(poll, 500);
    };
    timer = setTimeout(poll, 500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [automatic, observe, controller, plan?.id]);
  useEffect(() => { if (disabled) { controller.stop(); setAutomatic(false); } }, [disabled, controller]);
  const revisedFailures = () => {
    const result: Record<number, PlanStep> = {};
    for (const step of plan?.steps.filter(step => step.status === 'failed') ?? []) {
      const input = replacements[step.id];
      if (!input?.approved) throw new Error(`先明确修订失败步骤 ${step.id}`);
      if ('do' in step) {
        const liquid = liquidAction(step.do.action);
        const volume = Number(input.volume);
        if (liquid && (!input.volume || !Number.isFinite(volume) || volume <= 0)) throw new Error('根据已应用量与当前液体填写剩余操作量');
        if (liquid && !receipts[step.id]) throw new Error('原始操作 receipt 未知，禁止续行');
        result[step.id] = liquid ? literalRemainder(step, volume, input.direction) : step;
      } else result[step.id] = step;
    }
    return result;
  };
  const run = async (action: () => Promise<unknown>, success: string) => {
    if (pending || disabled) return;
    setPending(true);
    try { await action(); if (alive.current) setMessage(success); }
    catch (error) { controller.stop(); setAutomatic(false); if (alive.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { if (alive.current) setPending(false); }
  };
  const start = (auto: boolean) => run(async () => {
    await controller.arm(plan!.id, !auto && controlMode === 'manual' ? 'manual' : 'auto');
    if (!alive.current) { controller.stop(); return; }
    const status = await controller.advance(plan!.id);
    setMessage(status); setObserve(controller.observing); setAutomatic(auto && controller.dispatching);
    if (!auto) controller.stop();
  }, auto ? '自动派发已启动；正在观察当前步骤' : '已提交一个步骤；正在观察结果');
  return <section className="passage-controls" aria-label="传代控制">
    <h3>运行与交接</h3><p role="status">{message}</p>
    {disabled && <p>{disabled}</p>}
    {receiptError && <p role="alert">{receiptError}</p>}
    {controlMode === 'holding' && <p>已禁止先前控制继续操作；{controlSafe ? '源侧工作单元已停稳，确认仍会核验所有受管设备' : '正在建立物理保持目标并等待实测停稳，尚不可确认交接'}。</p>}
    {review && <div className="passage-review"><h4>现场核对</h4><ul>{review.facts.map(fact => <li key={`${fact.entity}:${fact.property}`}><code>{fact.entity}</code> · {fact.property}：{factLabel(fact.value)}</li>)}</ul><label><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} /> 已核对以上机械事实及当前物料、在途液体和占用</label></div>}
    {plan?.steps.filter(step => step.status === 'failed').map(step => {
      const operation = receipts[step.id];
      const liquid = 'do' in step && liquidAction(step.do.action);
      const snapshot = object(material?.values.materialSnapshot);
      const relevant = 'do' in step ? rows(snapshot?.vessels).filter(row => row.id === step.do.params.vessel || row.id === step.do.params.targetVessel) : [];
      return <fieldset key={step.id}><legend>修订失败步骤 {step.id} · {'do' in step ? step.do.action : '检查'}</legend>
        {liquid && <><p>原解析目标 {quantity(operation?.resolvedUl)} µL；已实转 {quantity(operation?.appliedUl)} µL；解析目标剩余 {quantity(operation?.remainingUl)} µL；分辨率余量 {quantity(operation?.resolutionResidualUl)} µL。以原 receipt 与当前器皿/吸头内容修订明确操作，不重新计算 fraction、top-up 或 all。</p>
          <table aria-label={`步骤 ${step.id} 当前物料`}><thead><tr><th>当前位置</th><th>实际液量 µL</th></tr></thead><tbody>{relevant.map(row => <tr key={text(row.id)}><td>{text(row.id)}</td><td>{quantity(row.volumeUl)}</td></tr>)}<tr><td>已装吸头</td><td>{quantity(object(snapshot?.tip)?.volumeUl)}</td></tr></tbody></table>
          <details><summary>原操作与余量明细</summary><dl>{['requestedUl', 'resolvedUl', 'appliedUl', 'observedUl', 'remainingUl', 'untransferredUl', 'resolutionResidualUl', 'quantityOutcome', 'remainderLocation'].map(key => <div key={key}><dt>{key}</dt><dd>{factLabel(operation?.[key])}</dd></div>)}</dl></details>
          {liquid === 'mix' && <label>修复方向<select value={replacements[step.id]?.direction ?? ''} onChange={event => setReplacements(previous => ({ ...previous, [step.id]: { ...previous[step.id], direction: event.target.value as 'aspirate' | 'dispense', approved: false } }))}><option value="">根据当前吸头内容选择</option><option value="aspirate">吸液</option><option value="dispense">分液</option></select></label>}
          <label>后续操作量（µL）<input type="number" min="0" step="any" value={replacements[step.id]?.volume ?? ''} onChange={event => setReplacements(previous => ({ ...previous, [step.id]: { ...previous[step.id], volume: event.target.value, approved: false } }))} /></label></>}
        {liquid && typeof operation?.remainingUl === 'number' && operation.remainingUl > 0 && <button onClick={() => setReplacements(previous => ({ ...previous, [step.id]: { ...previous[step.id], volume: String(operation.remainingUl), approved: false } }))}>填入原解析目标剩量 {quantity(operation.remainingUl)} µL</button>}
        <label><input type="checkbox" disabled={!!liquid && !operation} checked={replacements[step.id]?.approved ?? false} onChange={event => setReplacements(previous => ({ ...previous, [step.id]: { ...previous[step.id], volume: previous[step.id]?.volume ?? '', approved: event.target.checked } }))} /> 已核对原目标、实际量和当前余量差异；以新操作身份执行上述明确后续操作</label>
      </fieldset>;
    })}
    <div className="passage-buttons">
      <button disabled={!!disabled || pending || controlMode === 'holding'} onClick={() => void run(() => controller.requestControl(plan!.id, 'auto'), '已请求浏览器控制；先前控制已失效，等待源侧停稳并核对现场')}>申请浏览器控制</button>
      <button disabled={!!disabled || pending || controlMode === 'holding'} onClick={() => { controller.stop(); setAutomatic(false); void run(() => controller.requestControl(plan!.id, 'manual'), '接管请求已受理；已停止后续派发，尚未确认安全') }}>人工接管</button>
      <button disabled={!!disabled || pending} onClick={() => void run(() => controller.cancelActive(plan!.id), '取消已受理/运行调用；等待物理保持与调用终态，保留持物及液体')}>取消在途调用</button>
      <button disabled={!!disabled || pending || controlMode !== 'holding' || !controlSafe} onClick={() => void run(() => controller.confirmControl(plan!.id), '源侧已确认控制交接；当前物料与持物均保留')}>确认停稳交接</button>
      <button disabled={!!disabled || pending || !metadata.catalog} onClick={() => void run(async () => { setReview(await controller.review(plan!.id, metadata.catalog!)); setReviewed(false); }, '请核对机械事实及物料，确认后再准备未开始步骤')}>读取现场核对</button>
      <button disabled={!!disabled || pending || !reviewed || !review} onClick={() => void run(() => controller.prepareRemaining(plan!.id, review!, revisedFailures()), '已修订未开始步骤的控制身份；完成步骤及结果保留')}>准备未开始步骤</button>
      <button disabled={!!disabled || pending || automatic || !controlSafe || !['auto', 'manual'].includes(String(controlMode))} onClick={() => void start(false)}>执行一步</button>
      <button disabled={!!disabled || pending || automatic || !controlSafe || controlMode !== 'auto'} onClick={() => void start(true)}>自动推进</button>
      <button disabled={!!disabled || pending} onClick={() => { controller.stop(); setAutomatic(false); void run(() => controller.pause(), '仿真已暂停；暂停不代表调用被撤销或接管完成') }}>暂停</button>
      <button disabled={!!disabled || pending || view.sessionState !== 'PAUSED'} onClick={() => void run(() => controller.resume(), '仿真已恢复；继续先前已受理动作，未自动重发')}>恢复仿真</button>
    </div>
    <p>暂停冻结仿真时间。交接中的物理停稳需要仿真运行；恢复仿真后观察源侧状态。人工接管后须核对持物、吸头/在途液体、盖门、转子、占用与物料修订；失败或部分完成步骤不能直接重试。</p>
  </section>;
}
