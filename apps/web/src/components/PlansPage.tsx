import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { SessionClient } from '../lib/stream/client';
import { FrameRecorder } from '../lib/performance/frame-recorder';
import { useClientPlans, useClientView, useMotionPreview, usePlanDocument, usePlansList } from '../lib/plans/hooks';
import { previewBodies, type AvailablePreview, type PlanDocument } from '../lib/plans/model';
import { PlanChecklist, PlanStatus, defaultStep } from './PlanChecklist';
import { SceneViewport, type InspectVisuals, type InspectPreview, type RenderMetrics, type RenderedPose } from './viewer/SceneViewport';
import '../plans.css';

declare global { interface Window { __planViewDebug?: () => ReturnType<InspectPreview> | undefined } }
const planLayers = { colliders: false, sites: false, origins: false, grid: true };

export function PreviewNotice({ preview }: { preview: ReturnType<typeof useMotionPreview> }) {
  return <div className="plan-preview-notice" data-testid="plan-preview-status">
    {preview.value && <div className="plan-execution-mode" role="group" aria-label="执行方式">
      <span>执行方式</span><div><button type="button" aria-pressed={!preview.value.policyDriven} disabled={preview.value.policyDriven}
        title="按当前程序提供的预览显示">示教轨迹</button><button type="button" aria-pressed={preview.value.policyDriven} disabled={!preview.value.policyDriven}
        title="按当前程序提供的预览显示">策略</button></div></div>}
    {preview.loading ? <p role="status" aria-busy="true">正在读取当前关节状态的预览…</p> :
      preview.error ? <p role="status" className="plan-inline-error">路径暂不可用：{preview.error} <button onClick={preview.retry}>重试</button></p> :
        preview.explanation ? <p role="status">{preview.explanation}</p> : null}
  </div>;
}

export function PlanScene({ client, preview }: { client: SessionClient; preview?: AvailablePreview }) {
  const state = useClientView(client)!;
  const metrics = useRef<RenderMetrics>({ fps: 0, drawCalls: 0, objects: 0, applyMs: 0 });
  const rendered = useRef(new Map<string, RenderedPose>());
  const inspectVisuals = useRef<InspectVisuals | undefined>(undefined);
  const inspectPreview = useRef<InspectPreview | undefined>(undefined);
  const recorder = useMemo(() => new FrameRecorder(), []);
  const hidden = useMemo(() => new Set<string>(), []);
  const [selected, setSelected] = useState('');
  const [warnings, setWarnings] = useState<string[]>([]);
  const warn = useCallback((message: string) => setWarnings(prior => prior.includes(message) ? prior : [...prior, message]), []);
  useEffect(() => {
    const inspect = () => inspectPreview.current?.();
    window.__planViewDebug = inspect;
    return () => { if (window.__planViewDebug === inspect) delete window.__planViewDebug; };
  }, []);
  return <figure className="plan-figure" data-testid="plan-preview-figure"><header><div><span className="eyebrow">空间视口</span><h2>路径预览</h2></div><span className="plan-tag">Z-UP · m</span></header>
    <div className="plan-scene">{state.manifest ? <SceneViewport manifest={state.manifest} client={client}
      selected={selected} select={setSelected} layers={planLayers}
      {...{ hidden, warn, metrics, rendered, inspectVisuals, inspectPreview, recorder, preview }} previewFocus cameraPreset="isometric" compact /> :
      <div className="plan-empty" role="status">正在连接计划所属的空间会话…</div>}</div>
    <figcaption><span><b>图 1</b> · {preview ? preview.policyDriven ? '由策略执行 · 仅显示目标' : '预览 · 示教轨迹' : '选择机械臂步骤以查看路径'}</span>
      {preview && <span>{preview.policyDriven ? '目标位姿' : `${preview.points.length} 个 TCP 采样点 · ${state.manifest ? previewBodies(state.manifest, preview).length : 0} 个目标部件`} · sim {preview.simTime.toFixed(2)}s</span>}</figcaption>
    {warnings.length > 0 && <details className="plan-visual-notes"><summary>视觉提示 · {warnings.length}</summary>{warnings.map(message => <p key={message}>{message}</p>)}</details>}
  </figure>;
}

function PlanConnection({ id }: { id: string }) {
  const navigate = useNavigate(), search = useSearch({ strict: false });
  const [refreshKey, setRefreshKey] = useState('');
  const metadata = usePlanDocument(id, undefined, true, refreshKey);
  const sessionId = metadata.document?.sessionId;
  const client = useMemo(() => sessionId ? new SessionClient(sessionId) : undefined, [sessionId]);
  const state = useClientView(client);
  const entries = useClientPlans(client);
  const entry = entries.entries.find(value => value.plan.id === id);
  const stamp = `${state?.manifest?.sceneRevision}:${client?.buffer.epoch}:${state?.sessionState}:${entry?.sampleTimeMs}`;
  useEffect(() => { if (state?.manifest) setRefreshKey(stamp); }, [stamp, state?.manifest]);
  const [connectionError, setConnectionError] = useState<string>();
  useEffect(() => {
    if (!client) return;
    let disposed = false;
    setConnectionError(undefined);
    void client.start().catch((error: Error) => { if (!disposed) setConnectionError(error.message); });
    return () => { disposed = true; client.stop(); };
  }, [client]);
  const document = metadata.document;
  const plan = entry && entry.plan.revision >= (document?.revision ?? 0) ? entry.plan : document;
  const step = plan?.steps.find(value => value.id === search.step) ?? (plan ? defaultStep(plan) : undefined);
  const preview = useMotionPreview(client, plan, document, step, String(entry?.sampleTimeMs ?? ''));
  if (!plan || !client) return <section className="plan-empty" aria-busy={metadata.loading} role="status">{metadata.error ? <>
    无法读取此计划：{metadata.error} <button onClick={metadata.refresh}>重试</button></> : '正在读取计划…'}</section>;
  return <><div className="plan-context"><span>计划所属会话 <code>{sessionId}</code></span><Link to="/sessions/$sessionId" params={{ sessionId: sessionId! }}>打开工作台 ↗</Link>
    {connectionError && <span role="status">会话暂不可用：{connectionError}</span>}</div>
    <div className="plan-detail-grid"><PlanScene client={client} preview={preview.value} />
      <PlanChecklist {...{ client, plan, entry, document, previous: metadata.previous }} fullPage selected={step?.id}
        metadataError={metadata.error} metadataRetry={metadata.refresh} historyError={metadata.historyError}
        select={step => void navigate({ to: '/plans/$planId', params: { planId: id }, search: prior => ({ ...prior, step }) })}
        previewControls={<PreviewNotice preview={preview} />} /></div></>;
}

function StageStrip({ count }: { count?: number }) {
  return <div className="approval-stage-strip plan-stage-strip" role="group" aria-label="具身层阶段">{['素材', '场景', '计划', '作业', '评估', '策略'].map((label, index) => {
    const content = <><span className="approval-stage-label">0{index + 1} · {label}</span><strong className="approval-stage-value">{index === 2 ? count ?? '—' : '—'}</strong>
      <span className="approval-stage-state"><i aria-hidden="true" />{index === 2 ? '当前阶段 · 操作计划' : index < 2 ? '打开素材与场景' : '尚未上线'}</span>
      {index < 5 && <span className="approval-stage-arrow" aria-hidden="true">›</span>}</>;
    return index < 3 ? <Link key={label} className={`approval-stage ${index === 2 ? 'is-current' : ''}`} to={index === 0 ? '/assets' : index === 1 ? '/scenes' : '/plans'} aria-current={index === 2 ? 'step' : undefined}>{content}</Link> :
      <div key={label} className="approval-stage is-unavailable">{content}</div>;
  })}</div>;
}

export function PlansPage({ planId }: { planId?: string }) {
  const result = usePlansList(), navigate = useNavigate(), search = useSearch({ strict: false });
  const query = search.q ?? '', status = search.status ?? '';
  const plans = result.plans?.filter(value => (!status || value.status === status)
    && `${value.id} ${value.title} ${value.requestedBy.name}`.toLowerCase().includes(query.toLowerCase()));
  useEffect(() => { if (!planId && plans?.[0]) void navigate({ to: '/plans/$planId', params: { planId: plans[0].id }, replace: true,
    search: previous => ({ ...previous, step: undefined }) }); }, [planId, plans?.[0]?.id, navigate]);
  return <main className="plans-page" data-testid="plans-page"><header className="plans-heading"><div><span className="eyebrow">LAB OPERATIONS</span><h1>计划</h1><p>先写意图，再逐步执行。每一步都与真实结果相连。</p></div>
    <span className="plan-tag">操作计划 {result.plans?.length ?? '—'}</span></header><StageStrip count={result.plans?.length} />
    <section className="plan-list-panel" aria-label="计划列表"><div className="plan-list-toolbar"><h2>操作计划</h2><div><label>搜索<input aria-label="搜索计划" value={query} placeholder="名称、ID 或提交者"
      onChange={event => void navigate({ to: '.', search: prior => ({ ...prior, q: event.target.value || undefined }) })} /></label><label>状态<select aria-label="筛选计划状态" value={status}
        onChange={event => void navigate({ to: '.', search: prior => ({ ...prior, status: event.target.value || undefined }) })}>
        <option value="">全部状态</option>{['draft', 'submitted', 'approved', 'running', 'completed', 'abandoned'].map(value => <option key={value} value={value}>{({ draft: '草稿', submitted: '待审批', approved: '已批准', running: '运行中', completed: '已完成', abandoned: '已终止' } as Record<string, string>)[value]}</option>)}</select></label></div></div>
      {result.error && <p className="plan-inline-error" role="status">计划列表暂不可用：{result.error} <button onClick={result.retry}>重试</button></p>}
      {!result.plans ? <div className="plan-loading" role="status" aria-busy="true">正在读取计划…<i /><i /></div> : plans?.length ? <div className="plan-table-scroll"><table className="plan-table"><thead><tr><th>计划</th><th>名称与提交者</th><th>种类</th><th>状态</th><th className="number">修订</th><th>审批方式</th></tr></thead><tbody>{plans.map((plan: PlanDocument) =>
        <tr key={plan.id} className={plan.id === planId ? 'is-selected' : ''}><td><Link to="/plans/$planId" params={{ planId: plan.id }} search={prior => ({ ...prior, step: undefined })}><code>{plan.id}</code></Link></td>
          <td><Link to="/plans/$planId" params={{ planId: plan.id }} search={prior => ({ ...prior, step: undefined })}>{plan.title}</Link><small>{plan.requestedBy.kind} · {plan.requestedBy.name}</small></td>
          <td><span className="plan-tag">操作计划</span></td><td><PlanStatus status={plan.status} /></td><td className="number">r{plan.revision}</td><td>{plan.approvalMode === 'auto' ? '自动批准' : '人工审批'}</td></tr>)}</tbody></table></div> :
        <div className="plan-empty">{result.plans.length ? '没有符合筛选的计划。' : <>尚无操作计划。由 agent 使用 <code>lw plan create</code> 提交，计划会在这里显示。</>}</div>}</section>
    {planId && <PlanConnection key={planId} id={planId} />}
  </main>;
}
