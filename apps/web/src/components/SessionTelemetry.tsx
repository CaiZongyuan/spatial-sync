import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ComponentProps } from 'react';
import { request, type SessionSummary } from '../lib/api';
import { AlertContext, declaredRules, reading, type RuleDeclarations } from '../lib/live';
import type { SessionClient } from '../lib/stream/client';
import { deviceKey } from '../lib/info/binding';
import { SceneViewport } from './viewer/SceneViewport';
import { EntityPanel } from './EntityPanel';
import { AlertEpisodes, DeclaredRulesTable, DeviceReadings, useDeviceReadouts } from './DeviceTelemetry';
import '../live.css';

type ViewerState = Pick<ComponentProps<typeof SceneViewport>, 'metrics' | 'rendered' | 'inspectVisuals' | 'recorder'>;

function SessionDevices({ session, client, viewer }: { session: SessionSummary; client: SessionClient; viewer: ViewerState }) {
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const metadata = useSyncExternalStore(client.info.subscribeMetadata, client.info.getMetadata);
  const { active: activeAlerts, recent, critical, activeCapped, error: alertError } = useContext(AlertContext);
  const navigate = useNavigate(), search = useSearch({ strict: false });
  const selected = search.entity ?? '', readouts = search.readouts !== false;
  const manifest = state.manifest, catalog = metadata.catalog;
  const source = typeof session.source === 'string' ? session.source : session.source.kind;
  const [declarations, setDeclarations] = useState<RuleDeclarations>(), [ruleError, setRuleError] = useState<string>(), [retryRules, setRetryRules] = useState(0);
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
  const active = [...new Map([...(activeAlerts ?? []), ...(critical ? [critical] : [])]
    .filter(alert => alert.sessionId === session.sessionId).map(alert => [alert.alertId, alert])).values()];
  const episodes = [...new Map([...active, ...(recent?.filter(alert => alert.sessionId === session.sessionId) ?? [])].map(alert => [alert.alertId, alert])).values()];
  const alertKeys = new Set(active.map(alert => alert.entityKey));
  const devices = Object.values(catalog?.entities ?? {}).filter(binding =>
    Object.values(catalog?.types[binding.type]?.properties ?? {}).some(property => property.staleAfterSeconds !== undefined) || alertKeys.has(binding.key))
    .sort((a, b) => Number(alertKeys.has(b.key)) - Number(alertKeys.has(a.key)) || a.key.localeCompare(b.key));
  const readings = useDeviceReadouts(client, devices.map(binding => binding.key));
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
    void navigate({ to: '/sessions/$sessionId', params: { sessionId: session.sessionId }, search: previous => ({ ...previous, view: 'devices', entity: selected === owner ? undefined : owner || undefined }) });
  };
  const annotations = devices.map((binding, index) => {
    const values = readings[index];
    const alert = active.find(alert => alert.entityKey === binding.key);
    const details = Object.entries(values?.values ?? {}).filter(([, value]) => typeof value === 'number').slice(0, 2)
      .map(([name, value]) => `${reading(value)} ${catalog?.types[binding.type].properties[name]?.unit ?? name}`).join(' · ');
    return { key: binding.key, label: binding.key.split('/').at(-1)!, detail: readouts ? details : '', bodyKeys: bodyKeys.get(binding.key) ?? [],
      stale: Object.values(values?.meta ?? {}).some(meta => meta.stale), alert: alert ? (alert.acknowledgement ? 'acknowledged' : 'raised') as 'acknowledged' | 'raised' : undefined };
  });
  const highlightedKeys = useMemo(() => new Set(bodyKeys.get(selected) ?? []), [bodyKeys, selected]);
  const { metrics, rendered, inspectVisuals, recorder } = viewer;
  const hidden = useMemo(() => new Set<string>(), []), warn = useCallback(() => {}, []);
  const binding = catalog?.entities[selected];
  const historyBinding = devices.find(binding => binding.key === selected) ?? devices[0];
  const historyProperty = historyBinding && Object.entries(catalog!.types[historyBinding.type].properties).find(([, property]) => property.schema.type === 'number' || property.schema.type === 'integer')?.[0];
  const command = historyBinding && historyProperty ? `lw history ${historyBinding.key} ${historyProperty} --since 1h --every 1m --agg mean` : undefined;
  const [copied, setCopied] = useState('');
  return <>
    <header className="overview-page-heading"><h1>会话 · 仿真设备</h1><p><code>{session.sceneVersion ?? session.sceneId}</code><span>·</span><span>当前会话</span><code>{session.sessionId}</code><span className="reference-chip"><i />{state.sessionState} · {reading(state.simTime)} s</span></p></header>
    {(state.error || state.infoError) && <div role="alert" className="platform-error">{state.error || state.infoError}</div>}
    <div className="overview-hero live-hero"><figure className="overview-figure"><div className="overview-stage">
      {manifest ? <SceneViewport {...{ manifest, client, selected, hidden, warn, metrics, rendered, inspectVisuals, recorder, annotations, highlightedKeys }} select={choose}
        layers={{ colliders: false, sites: false, origins: false, grid: true }} cameraPreset="isometric" compact /> : <div className="overview-loading" role="status">正在读取空间状态…</div>}
    </div><figcaption><strong>图 1</strong><span>{manifest?.sceneName ?? '当前场景'} · 红点 = 活动告警 · 点击设备</span>
      <button className="readout-switch" aria-label="读数" aria-pressed={readouts} onClick={() => void navigate({ to: '/sessions/$sessionId', params: { sessionId: session.sessionId }, search: previous => ({ ...previous, view: 'devices', readouts: readouts ? false : undefined }) })}><i />读数</button>
      <span className="reference-tag">仿真会话</span></figcaption></figure>
      <aside className="overview-device-drawer live-drawer" aria-label="设备抽屉" data-connection={state.connection}>
        {binding && catalog ? <><div className="drawer-heading"><span className="eyebrow">仿真设备</span><button className="reference-link" onClick={() => choose('')}>× 关闭</button></div>
          <h2>{binding.key.split('/').at(-1)}</h2><code className="drawer-key">{binding.key}</code><span className="reference-tag">{binding.type}</span>
          <DeviceReadings {...{ client, catalog, binding, rules }} trends={false} />
          <EntityPanel store={client.info} binding={binding} definition={catalog.types[binding.type]} compact
            disabled={state.connection !== 'connected' || state.role !== 'operator' || !metadata.ready || !['RUNNING', 'PAUSED'].includes(state.sessionState)}
            onAction={(key, action, params) => client.action(key, action, params)} /></> : <><span className="eyebrow">设备抽屉</span><h2>点击地图上的设备</h2><p className="drawer-instruction">查看实时读数与下方的 1 小时历史。红点表示活动告警，空心点表示已确认。</p>
            <div className="device-legend">{devices.map(binding => <button key={binding.key} onClick={() => choose(binding.key)}><i className={alertKeys.has(binding.key) ? 'has-alert' : ''} /><span>{binding.key.split('/').at(-1)}</span><code>{binding.type}</code></button>)}</div>
            {metadata.ready && !devices.length && <p className="live-muted">此场景未绑定遥测设备。可从场景库启动 Synthetic Laboratory。</p>}</>}
      </aside></div>
    <section className="live-section" aria-label="告警"><header><h2>告警</h2><span className="eyebrow">{activeAlerts ? `${activeCapped ? '活动查询已达上限 · ' : ''}${active.length} 条已获取活动 · 最近全局 100 条中的当前会话记录` : '正在读取告警…'}</span></header>
      <AlertEpisodes episodes={episodes} error={alertError} loaded={Boolean(activeAlerts)}
        empty="当前会话没有活动或最近告警。设备规则持续满足条件后会显示在这里。" />
      <DeclaredRulesTable rules={rules} error={ruleError} loading={!declarations || !catalog} retry={() => setRetryRules(retryRules + 1)} />
      <p className="live-inbox-hint">agent 每轮先查 <code>lw inbox --since &lt;cursor&gt;</code>，再读取设备当前值与历史。</p>
    </section>
    <section className="live-section" aria-label="设备"><header><h2>设备</h2><span className="eyebrow">1 小时查询窗口 · 1 分钟均值 · 只标实际数据跨度</span></header>
      <div className="live-device-grid">{devices.map(binding => <article className="live-device-card" key={binding.key} data-testid={`live-device-${binding.key}`}><header><button className="reference-link" onClick={event => { choose(binding.key); event.currentTarget.closest('.session-telemetry')?.scrollTo({ top: 0, behavior: 'smooth' }); }}>{binding.key.split('/').at(-1)}</button><span className={`live-chip ${alertKeys.has(binding.key) ? 'severity-critical' : ''}`}><i />{alertKeys.has(binding.key) ? '告警中' : '无活动告警'}</span></header><code className="drawer-key">{binding.key}</code><span className="reference-tag">{binding.type}</span><DeviceReadings {...{ client, catalog: catalog!, binding, rules }} /></article>)}</div>
      {metadata.ready && !devices.length && <p className="live-empty">当前场景没有声明带新鲜度的遥测属性。</p>}
    </section>
    <section className="live-section live-source-history"><div><header><h2>来源</h2></header><dl className="reference-kv"><div><dt>宿主</dt><dd>当前仿真会话 · {source}</dd></div><div><dt>Device Host / broker</dt><dd>尚未接入真实设备</dd></div><div><dt>标识空间</dt><dd><code>/World/…</code> · 与真实设备 <code>/Real/…</code> 分离</dd></div><div><dt>场景版本</dt><dd><code>{session.sceneVersion ?? '未提供'}</code></dd></div><div><dt>历史来源</dt><dd>当前会话录制</dd></div><div><dt>保留策略</dt><dd>由网关配置 · 当前接口未公开</dd></div></dl></div>
      <div><header><h2>历史</h2></header><p className="live-muted">查询最近 1 小时，每分钟聚合；会话不足 1 小时时仅展示已录制数据。过期读数与曲线以灰色虚线显示。</p>
        {command ? <div className="live-command"><code>$ {command}</code><button onClick={() => { void navigator.clipboard.writeText(command).then(() => setCopied(command)).catch(() => setCopied('复制失败，请手动选择命令')); }}>{copied === command ? '已复制' : '复制'}</button></div> : <p className="live-muted">绑定数值属性后提供历史命令。</p>}
        {copied && copied !== command && <span role="status">{copied}</span>}<Link to="/recordings" className="reference-link">查看会话录制 →</Link>
      </div></section>
  </>;
}

export function SessionTelemetry({ client, viewer }: { client: SessionClient; viewer: ViewerState }) {
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const [session, setSession] = useState<SessionSummary>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    setSession(undefined); setError(undefined);
    void request<SessionSummary>(`/sessions/${encodeURIComponent(client.sessionId)}`, { signal: controller.signal })
      .then(setSession).catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [client.sessionId, state.manifest?.sceneRevision]);
  return <div className="overview-page live-page session-telemetry">
    {error ? <p role="alert" className="platform-error">{error}</p> : session ?
      <SessionDevices session={session} client={client} viewer={viewer} /> : <p role="status">正在读取会话设备…</p>}
  </div>;
}
