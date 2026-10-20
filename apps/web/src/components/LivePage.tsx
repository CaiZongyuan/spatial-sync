import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { useCallback, useContext, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { request, type SessionSummary } from '../lib/api';
import type { Catalog, EntityBinding } from '../lib/protocol';
import { AlertContext, declaredRules, elapsed, reading, sparkline, type DeclaredRule, type HistoryPoint, type RuleDeclarations } from '../lib/live';
import { SessionClient } from '../lib/stream/client';
import { deviceKey } from '../lib/info/binding';
import { useEntity } from '../lib/info/hooks';
import { FrameRecorder } from '../lib/performance/frame-recorder';
import { SceneViewport, type InspectVisuals, type RenderedPose, type RenderMetrics } from './viewer/SceneViewport';
import { AlertAcknowledgement, Severity } from './Alerts';
import { EntityPanel } from './EntityPanel';
import '../live.css';

function Trend({ sessionId, entityKey, property, stale }: { sessionId: string; entityKey: string; property: string; stale: boolean }) {
  const [history, setHistory] = useState<{ points?: HistoryPoint[]; error?: string }>({});
  const [retry, setRetry] = useState(0);
  const id = useId();
  useEffect(() => {
    let disposed = false, pending: AbortController | undefined;
    setHistory({});
    const refresh = async () => {
      if (pending) return;
      const controller = new AbortController(); pending = controller;
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const query = new URLSearchParams({ sessionId, key: entityKey, property, since: '1h', every: '1m', agg: 'mean' });
        const points = await request<HistoryPoint[]>(`/history?${query}`, { signal: controller.signal });
        if (!disposed) setHistory({ points });
      } catch (error) { if (!disposed) setHistory({ error: (error as Error).message }); }
      finally { clearTimeout(timeout); pending = undefined; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 15000);
    return () => { disposed = true; clearInterval(timer); pending?.abort(); };
  }, [sessionId, entityKey, property, retry]);
  const chart = history.points && sparkline(history.points);
  if (history.error) return <div className="trend-status" role="status">历史不可用 · {history.error}<button className="reference-link" onClick={() => setRetry(retry + 1)}>重试</button></div>;
  if (!chart) return <div className="trend-status">{history.points ? '暂无数值历史' : '正在读取历史…'}</div>;
  return <div className={`live-trend ${stale ? 'is-stale' : ''}`} data-testid={`trend-${entityKey}-${property}`}>
    <svg viewBox="0 0 280 64" role="img" aria-label={`${property} 实际历史 ${elapsed(chart.end - chart.start)}，最小 ${reading(chart.low)}，最大 ${reading(chart.high)}`}>
      <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".18" /><stop offset="100%" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs>
      <path className="trend-grid" d="M6 7H230 M6 28H230 M6 49H230" />
      {chart.count > 1 && <path d={`${chart.path} L${chart.last[0]},49 L6,49 Z`} fill={`url(#${id})`} />}
      <path className="trend-line" d={chart.path} />
      <circle cx={chart.last[0]} cy={chart.last[1]} r="2.8" fill="currentColor" />
      <text x="236" y="12">{reading(chart.high)}</text>{chart.low !== chart.high && <text x="236" y="50">{reading(chart.low)}</text>}
      {chart.start === chart.end ? <text x="118" y="62" textAnchor="middle">0 秒</text> : <><text x="6" y="62">0</text><text x="230" y="62" textAnchor="end">{elapsed(chart.end - chart.start)}</text></>}
    </svg>
    <span>{chart.count} 个聚合点 · 实际跨度 {elapsed(chart.end - chart.start)}</span>
  </div>;
}

function DeviceReadings({ client, catalog, binding, trends = true }: { client: SessionClient; catalog: Catalog; binding: EntityBinding; trends?: boolean }) {
  const entity = useEntity(client.info, binding.key);
  const definition = catalog.types[binding.type];
  return <div className="live-properties">{Object.entries(definition.properties).map(([name, property]) => {
    const value = entity?.values[name], meta = entity?.meta[name], stale = meta?.stale === true;
    return <div key={name} className={`live-property ${stale ? 'is-stale' : ''}`} data-testid={`reading-${binding.key}-${name}`}>
      <div className="live-property-main"><div><code>{name}</code></div>
        <div className="live-reading"><b>{reading(value)}</b>{property.unit && <span>{property.unit}</span>}</div>
        {stale && <span className="live-chip stale-chip"><i />过期</span>}
      </div>
      {trends && (property.schema.type === 'number' || property.schema.type === 'integer') &&
        <Trend sessionId={client.sessionId} entityKey={binding.key} property={name} stale={stale} />}
    </div>;
  })}</div>;
}

function DeclaredRulesTable({ rules, error, loading, retry }: { rules: DeclaredRule[]; error?: string; loading: boolean; retry: () => void }) {
  return <div className="declared-rules"><h3 className="eyebrow">已声明的告警规则 · 当前场景版本</h3>
    {error ? <p role="status" className="live-muted">规则暂不可用 · {error} <button className="reference-link" onClick={retry}>重试</button></p> : loading ? <p className="live-muted">正在读取作者声明…</p> : rules.length ?
      <div className="live-table-scroll"><table className="live-table"><thead><tr><th>设备 / 规则</th><th>条件</th><th>持续</th><th>严重度</th></tr></thead><tbody>{rules.map(rule =>
        <tr key={`${rule.key}-${rule.name}`}><td><code>{rule.key}</code><small>{rule.name}</small></td><td><span>{rule.message}</span><details><summary>查看条件</summary><code>{JSON.stringify(rule.when)}</code>{rule.hint && <p>{rule.hint}</p>}</details></td><td className="live-number">{reading(rule.forSeconds)} s</td><td><Severity severity={rule.severity} /></td></tr>)}</tbody></table></div>
      : <p className="live-muted">此场景版本未声明告警规则。</p>}
  </div>;
}

function LiveSession({ session }: { session: SessionSummary }) {
  const client = useMemo(() => new SessionClient(session.sessionId), [session.sessionId]);
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const metadata = useSyncExternalStore(client.info.subscribeMetadata, client.info.getMetadata);
  const { active: activeAlerts, recent, activeCapped, error: alertError } = useContext(AlertContext);
  const [error, setError] = useState('');
  const navigate = useNavigate(), search = useSearch({ strict: false });
  const selected = search.entity ?? '', readouts = search.readouts !== false;
  const manifest = state.manifest, catalog = metadata.catalog;
  const source = typeof session.source === 'string' ? session.source : session.source.kind;
  const [declarations, setDeclarations] = useState<RuleDeclarations>(), [ruleError, setRuleError] = useState<string>(), [retryRules, setRetryRules] = useState(0);
  useEffect(() => {
    let disposed = false;
    void client.start().catch((error: Error) => { if (!disposed) setError(error.message); });
    return () => { disposed = true; client.stop(); };
  }, [client]);
  useEffect(() => {
    const controller = new AbortController();
    setDeclarations(undefined); setRuleError(undefined);
    const [name, rev] = (session.sceneVersion ?? '').split('@');
    if (!name || !rev) { setRuleError('会话未提供固定场景版本'); return; }
    void request<RuleDeclarations>(`/library/scenes/${encodeURIComponent(name)}/${encodeURIComponent(rev)}?source=${encodeURIComponent(source)}`, { signal: controller.signal })
      .then(value => {
        if (session.sceneContentHash && value.contentHash !== session.sceneContentHash) throw new Error('场景内容与会话固定版本不一致');
        setDeclarations(value);
      }).catch((error: Error) => { if (!controller.signal.aborted) setRuleError(error.message); });
    return () => controller.abort();
  }, [session.sceneVersion, session.sceneContentHash, source, retryRules]);
  const rules = useMemo(() => declarations && catalog ? declaredRules(declarations, catalog, source) : [], [declarations, catalog, source]);
  const active = activeAlerts?.filter(alert => alert.sessionId === session.sessionId) ?? [];
  const episodes = [...new Map([...active, ...(recent?.filter(alert => alert.sessionId === session.sessionId) ?? [])].map(alert => [alert.alertId, alert])).values()];
  const alertKeys = new Set(active.map(alert => alert.entityKey));
  const devices = Object.values(catalog?.entities ?? {}).filter(binding =>
    Object.values(catalog?.types[binding.type]?.properties ?? {}).some(property => property.staleAfterSeconds !== undefined) || alertKeys.has(binding.key))
    .sort((a, b) => Number(alertKeys.has(b.key)) - Number(alertKeys.has(a.key)) || a.key.localeCompare(b.key));
  const bodyKeys = useMemo(() => {
    const result = new Map<string, string[]>();
    if (manifest && catalog) for (const node of manifest.nodes) {
      if (node.kind === 'group') continue;
      const owner = deviceKey(manifest, catalog, node.entityKey);
      if (owner) result.set(owner, [...(result.get(owner) ?? []), node.entityKey]);
    }
    return result;
  }, [manifest, catalog]);
  const choose = (key: string) => {
    const owner = manifest ? deviceKey(manifest, catalog, key) ?? key : key;
    void navigate({ to: '/live', search: previous => ({ ...previous, entity: selected === owner ? undefined : owner || undefined }) });
  };
  const annotations = devices.map(binding => {
    const values = client.info.getEntity(binding.key);
    const alert = active.find(alert => alert.entityKey === binding.key);
    const details = Object.entries(values?.values ?? {}).filter(([, value]) => typeof value === 'number').slice(0, 2)
      .map(([name, value]) => `${reading(value)} ${catalog?.types[binding.type].properties[name]?.unit ?? name}`).join(' · ');
    return { key: binding.key, label: binding.key.split('/').at(-1)!, detail: readouts ? details : '', bodyKeys: bodyKeys.get(binding.key) ?? [],
      stale: Object.values(values?.meta ?? {}).some(meta => meta.stale), alert: alert ? (alert.acknowledgement ? 'acknowledged' : 'raised') as 'acknowledged' | 'raised' : undefined };
  });
  const highlightedKeys = useMemo(() => new Set(bodyKeys.get(selected) ?? []), [bodyKeys, selected]);
  const metrics = useRef<RenderMetrics>({ fps: 0, drawCalls: 0, objects: 0, applyMs: 0 }), rendered = useRef(new Map<string, RenderedPose>()), inspectVisuals = useRef<InspectVisuals | undefined>(undefined);
  const recorder = useMemo(() => new FrameRecorder(), []), hidden = useMemo(() => new Set<string>(), []), warn = useCallback(() => {}, []);
  const binding = catalog?.entities[selected];
  const historyBinding = devices.find(binding => binding.key === selected) ?? devices[0];
  const historyProperty = historyBinding && Object.entries(catalog!.types[historyBinding.type].properties).find(([, property]) => property.schema.type === 'number' || property.schema.type === 'integer')?.[0];
  const command = historyBinding && historyProperty ? `lw history ${historyBinding.key} ${historyProperty} --since 1h --every 1m --agg mean` : undefined;
  const [copied, setCopied] = useState('');
  return <>
    <header className="overview-page-heading"><h1>实况 · 仿真设备</h1><p><code>{session.sceneVersion ?? session.sceneId}</code><span>·</span><span>当前会话</span><code>{session.sessionId}</code><span className="reference-chip"><i />{state.sessionState} · {reading(state.simTime)} s</span></p></header>
    {(error || state.error || state.infoError) && <div role="alert" className="platform-error">{error || state.error || state.infoError}</div>}
    <div className="overview-hero live-hero"><figure className="overview-figure"><div className="overview-stage">
      {manifest ? <SceneViewport {...{ manifest, client, selected, hidden, warn, metrics, rendered, inspectVisuals, recorder, annotations, highlightedKeys }} select={choose}
        layers={{ colliders: false, sites: false, origins: false, grid: true }} cameraPreset="isometric" compact /> : <div className="overview-loading" role="status">正在读取空间状态…</div>}
    </div><figcaption><strong>图 1</strong><span>{manifest?.sceneName ?? '当前场景'} · 红点 = 活动告警 · 点击设备</span>
      <button className="readout-switch" aria-label="读数" aria-pressed={readouts} onClick={() => void navigate({ to: '/live', search: previous => ({ ...previous, readouts: readouts ? false : undefined }) })}><i />读数</button>
      <span className="reference-tag">仿真会话</span></figcaption></figure>
      <aside className="overview-device-drawer live-drawer" aria-label="设备抽屉" data-connection={state.connection}>
        {binding && catalog ? <><div className="drawer-heading"><span className="eyebrow">仿真设备</span><button className="reference-link" onClick={() => choose('')}>× 关闭</button></div>
          <h2>{binding.key.split('/').at(-1)}</h2><code className="drawer-key">{binding.key}</code><span className="reference-tag">{binding.type}</span>
          <DeviceReadings {...{ client, catalog, binding }} trends={false} />
          <EntityPanel store={client.info} binding={binding} definition={catalog.types[binding.type]} compact
            disabled={state.connection !== 'connected' || state.role !== 'operator' || !metadata.ready || !['RUNNING', 'PAUSED'].includes(state.sessionState)}
            onAction={(key, action, params) => client.action(key, action, params)} /></> : <><span className="eyebrow">设备抽屉</span><h2>点击地图上的设备</h2><p className="drawer-instruction">查看实时读数与下方的 1 小时历史。红点表示活动告警，空心点表示已确认。</p>
            <div className="device-legend">{devices.map(binding => <button key={binding.key} onClick={() => choose(binding.key)}><i className={alertKeys.has(binding.key) ? 'has-alert' : ''} /><span>{binding.key.split('/').at(-1)}</span><code>{binding.type}</code></button>)}</div>
            {metadata.ready && !devices.length && <p className="live-muted">此场景未绑定遥测设备。可从场景库启动 Synthetic Laboratory。</p>}</>}
      </aside></div>
    <section className="live-section" aria-label="告警"><header><h2>告警</h2><span className="eyebrow">{activeAlerts ? `${activeCapped ? '活动查询已达上限 · ' : ''}${active.length} 条已获取活动 · 最近全局 100 条中的当前会话记录` : '正在读取告警…'}</span></header>
      {alertError ? <p role="status" className="platform-error">告警暂不可用 · {alertError} · 自动重试中</p> : episodes.length ? <div className="live-table-scroll"><table className="live-table" data-testid="live-alerts"><thead><tr><th>严重度</th><th>设备 / 规则</th><th>读数与开始</th><th>状态 / 确认</th></tr></thead><tbody>{episodes.map(alert => <tr key={alert.alertId} data-alert-id={alert.alertId}>
        <td><Severity severity={alert.severity} /></td><td><code>{alert.entityKey}</code><span>{alert.message}</span><small>{alert.rule}</small>{alert.hint && <small>{alert.hint}</small>}</td>
        <td className="live-number">{reading(alert.value)}<small>自 {reading(alert.since)} s 起</small></td><td><span className="live-alert-state">{alert.active ? alert.acknowledgement ? '活动 · 已确认' : '活动 · 待确认' : alert.closedContext ? '上下文结束' : '已恢复'}</span><AlertAcknowledgement alert={alert} /></td></tr>)}</tbody></table></div> : activeAlerts && <p className="live-empty">当前会话没有活动或最近告警。设备规则持续满足条件后会显示在这里。</p>}
      <DeclaredRulesTable rules={rules} error={ruleError} loading={!declarations || !catalog} retry={() => setRetryRules(retryRules + 1)} />
      <p className="live-inbox-hint">agent 每轮先查 <code>lw inbox --since &lt;cursor&gt;</code>，再读取设备当前值与历史。</p>
    </section>
    <section className="live-section" aria-label="设备"><header><h2>设备</h2><span className="eyebrow">1 小时查询窗口 · 1 分钟均值 · 只标实际数据跨度</span></header>
      <div className="live-device-grid">{devices.map(binding => <article className="live-device-card" key={binding.key} data-testid={`live-device-${binding.key}`}><header><button className="reference-link" onClick={() => { choose(binding.key); window.scrollTo({ top: 0, behavior: 'smooth' }); }}>{binding.key.split('/').at(-1)}</button><span className={`live-chip ${alertKeys.has(binding.key) ? 'severity-critical' : ''}`}><i />{alertKeys.has(binding.key) ? '告警中' : '无活动告警'}</span></header><code className="drawer-key">{binding.key}</code><span className="reference-tag">{binding.type}</span><DeviceReadings {...{ client, catalog: catalog!, binding }} /></article>)}</div>
      {metadata.ready && !devices.length && <p className="live-empty">当前场景没有声明带新鲜度的遥测属性。</p>}
    </section>
    <section className="live-section live-source-history"><div><header><h2>来源</h2></header><dl className="reference-kv"><div><dt>宿主</dt><dd>当前仿真会话 · {source}</dd></div><div><dt>Device Host / broker</dt><dd>尚未接入真实设备</dd></div><div><dt>标识空间</dt><dd><code>/World/…</code> · 与真实设备 <code>/Real/…</code> 分离</dd></div><div><dt>场景版本</dt><dd><code>{session.sceneVersion ?? '未提供'}</code></dd></div><div><dt>历史来源</dt><dd>当前会话录制</dd></div><div><dt>保留策略</dt><dd>由网关配置 · 当前接口未公开</dd></div></dl></div>
      <div><header><h2>历史</h2></header><p className="live-muted">查询最近 1 小时，每分钟聚合；会话不足 1 小时时仅展示已录制数据。过期读数与曲线以灰色虚线显示。</p>
        {command ? <div className="live-command"><code>$ {command}</code><button onClick={() => { void navigator.clipboard.writeText(command).then(() => setCopied(command)).catch(() => setCopied('复制失败，请手动选择命令')); }}>{copied === command ? '已复制' : '复制'}</button></div> : <p className="live-muted">绑定数值属性后提供历史命令。</p>}
        {copied && copied !== command && <span role="status">{copied}</span>}<Link to="/recordings" className="reference-link">查看会话录制 →</Link>
      </div></section>
  </>;
}

export function LivePage() {
  const [session, setSession] = useState<SessionSummary>(), [error, setError] = useState<string>(), [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let disposed = false, pending: AbortController | undefined, current = '';
    const refresh = async () => {
      if (pending) return;
      const controller = new AbortController(); pending = controller;
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const health = await request<{ sessions: { sessionId: string; state: string }[] }>('/health', { signal: controller.signal });
        const active = health.sessions.find(session => ['RUNNING', 'PAUSED'].includes(session.state));
        if (!active) { if (!disposed) { setSession(undefined); current = ''; } }
        else if (active.sessionId !== current) {
          const value = await request<SessionSummary>(`/sessions/${encodeURIComponent(active.sessionId)}`, { signal: controller.signal });
          if (!disposed && ['RUNNING', 'PAUSED'].includes(value.state)) { setSession(value); current = value.sessionId; }
        }
        if (!disposed) { setError(undefined); setLoaded(true); }
      } catch (error) { if (!disposed) { setError((error as Error).message); setLoaded(true); setSession(undefined); current = ''; } }
      finally { clearTimeout(timeout); pending = undefined; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 2000);
    return () => { disposed = true; clearInterval(timer); pending?.abort(); };
  }, []);
  return <main className="overview-page live-page">{error && <div className="platform-error" role="alert">{error} · 自动重试中</div>}
    {session ? <LiveSession key={session.sessionId} session={session} /> : <><header className="overview-page-heading"><h1>实况 · 仿真设备</h1><p>查看当前仿真会话中的设备、告警与历史。</p></header><section className="live-empty live-welcome"><span className="empty-scene-mark" aria-hidden="true">◇</span><h2>{loaded ? '等待活动仿真会话' : '正在连接实验室'}</h2><p>启动带遥测设备的场景后，真实状态会显示在这里。</p><Link to="/scenes" className="button-link primary">去场景库启动 →</Link></section></>}
  </main>;
}
