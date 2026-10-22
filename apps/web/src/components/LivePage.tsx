import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { useContext, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { LiveClient } from '../lib/live-client';
import { AlertContext, type DeclaredRule } from '../lib/live';
import { AlertEpisodes, DeclaredRulesTable, DeviceReadings } from './DeviceTelemetry';
import { EntityPanel } from './EntityPanel';
import { ApprovalBanner, InvocationsPanel } from './InvocationsPanel';
import { LiveLayout } from './LiveLayout';
import { SessionPlans, useSessionPlan } from './SessionPlans';
import '../live.css';

export function LivePage() {
  const client = useMemo(() => new LiveClient(), []);
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const status = useSyncExternalStore(client.subscribe, client.getLiveStatus);
  const metadata = useSyncExternalStore(client.info.subscribeMetadata, client.info.getMetadata);
  const catalog = metadata.catalog;
  const navigate = useNavigate(), search = useSearch({ strict: false });
  const selected = search.entity ?? '', readouts = search.readouts !== false;
  const planSelection = useSessionPlan(client, search.plan, search.step);
  const { active: allActive, recent, critical, activeCapped, error: alertError } = useContext(AlertContext);
  const [copied, setCopied] = useState('');
  useEffect(() => {
    void client.start();
    return () => client.stop();
  }, [client]);
  const active = [...new Map([...(allActive ?? []), ...(critical ? [critical] : [])]
    .filter(alert => alert.sessionId === status?.id).map(alert => [alert.alertId, alert])).values()];
  const episodes = [...new Map([...active, ...(recent?.filter(alert => alert.sessionId === status?.id) ?? [])]
    .map(alert => [alert.alertId, alert])).values()];
  const alertKeys = new Set(active.map(alert => alert.entityKey));
  const devices = Object.values(catalog?.entities ?? {}).sort((a, b) =>
    Number(alertKeys.has(b.key)) - Number(alertKeys.has(a.key)) || a.key.localeCompare(b.key));
  const rules = useMemo<DeclaredRule[]>(() => Object.entries(status?.rules ?? {}).flatMap(([key, declarations]) =>
    Object.entries(declarations).map(([name, rule]) => ({ ...rule, key, name }))), [status?.rules]);
  const choose = (key: string) => void navigate({ to: '/live',
    search: previous => ({ ...previous, entity: selected === key ? undefined : key || undefined }) });
  const binding = catalog?.entities[selected];
  const historyBinding = binding ?? devices[0];
  const historyProperty = historyBinding && Object.entries(catalog!.types[historyBinding.type].properties)
    .find(([, property]) => property.schema.type === 'number' || property.schema.type === 'integer')?.[0];
  const command = historyBinding && historyProperty ?
    `lw history ${historyBinding.key} ${historyProperty} --since 1h --every 1m --agg mean` : undefined;
  const disabled = state.connection !== 'connected' || state.role !== 'operator'
    || state.sessionState !== 'RUNNING' || !metadata.ready;
  const error = state.error === 'Live is not configured' ? '尚未配置实验室实况。' : state.error;
  return <main className="overview-page live-page">
    <header className="overview-page-heading"><h1>实况</h1><p><code>{status?.name ?? '实验室设备'}</code>
      <span className="reference-chip"><i />{state.connection === 'connected' ? status?.state : '等待连接'}</span>
      <span>独立运行 · 真实设备</span></p></header>
    {(error || state.infoError) && <div className="platform-error" role="alert">{error || state.infoError}</div>}
    <ApprovalBanner store={client.info} disabled={disabled}
      onDecision={(id, decision) => client.approveInvocation(id, decision)} />
    <div className="overview-hero live-hero">
      <LiveLayout {...{ client, catalog, status, selected, readouts, active }} select={choose}
        toggleReadouts={() => void navigate({ to: '/live',
          search: previous => ({ ...previous, readouts: readouts ? false : undefined }) })} />
      <aside className="overview-device-drawer live-drawer" aria-label="设备抽屉" data-connection={state.connection}>
        {binding && catalog ? <><div className="drawer-heading"><span className="eyebrow">实况设备</span>
          <button className="reference-link" onClick={() => choose('')}>× 关闭</button></div>
          <h2>{binding.key.split('/').at(-1)}</h2><code className="drawer-key">{binding.key}</code>
          <span className="reference-tag">{binding.type}</span>
          <DeviceReadings {...{ client, catalog, binding, rules }} trends={false} />
          <EntityPanel store={client.info} binding={binding} definition={catalog.types[binding.type]} compact
            disabled={disabled} onAction={(key, action, params) => client.action(key, action, params)} /></> :
          <><span className="eyebrow">设备抽屉</span><h2>选择实况设备</h2>
            <p className="drawer-instruction">查看当前读数、过去 1 小时的趋势与设备操作。高风险操作需要人工审批。</p>
            <div className="device-legend">{devices.map(binding => <button key={binding.key}
              onClick={() => choose(binding.key)}><i className={alertKeys.has(binding.key) ? 'has-alert' : ''} />
              <span>{binding.key.split('/').at(-1)}</span><code>{binding.type}</code></button>)}</div>
            {!devices.length && <p className="live-muted">实况连接后，设备会显示在这里，无需启动仿真会话。</p>}</>}
      </aside>
    </div>
    <section className="live-section" aria-label="告警"><header><h2>告警</h2><span className="eyebrow">
      {allActive ? `${activeCapped ? '活动查询已达上限 · ' : ''}${active.length} 条已获取活动 · 实况设备` : '正在读取告警…'}
    </span></header><AlertEpisodes {...{ episodes }} error={alertError} loaded={Boolean(allActive)}
      empty="实况设备没有活动或最近告警。" />
      <DeclaredRulesTable rules={rules} loading={!status || !catalog} retry={() => { void client.start(); }} />
      <p className="live-inbox-hint">agent 每轮先查 <code>lw inbox --since &lt;cursor&gt;</code>，再读取设备当前值与历史。</p>
    </section>
    <section className="live-section" aria-label="设备"><header><h2>设备</h2>
      <span className="eyebrow">1 小时查询窗口 · 1 分钟均值 · 只标实际数据跨度</span></header>
      <div className="live-device-grid">{devices.map(binding => {
        const alert = active.find(alert => alert.entityKey === binding.key);
        return <article className="live-device-card" key={binding.key} data-testid={`live-device-${binding.key}`}>
          <header><button className="reference-link" onClick={() => { choose(binding.key);
            window.scrollTo({ top: 0, behavior: 'smooth' }); }}>{binding.key.split('/').at(-1)}</button>
            <span className={`live-chip ${alert ? `severity-${alert.severity}` : ''}`}><i />
              {alert ? '告警中' : '无活动告警'}</span></header><code className="drawer-key">{binding.key}</code>
          <span className="reference-tag">{binding.type}</span>
          <DeviceReadings {...{ client, catalog: catalog!, binding, rules }} />
        </article>;
      })}</div>
    </section>
    <section className="live-section" aria-label="操作计划"><SessionPlans client={client} selection={planSelection}
      selectPlan={plan => void navigate({ to: '/live', search: previous => ({ ...previous, plan, step: undefined }) })}
      selectStep={step => void navigate({ to: '/live', search: previous => ({ ...previous, step }) })} /></section>
    <InvocationsPanel store={client.info} {...{ selected, simTime: state.simTime }} disabled={disabled}
      onCancel={id => client.cancelInvocation(id)} />
    <section className="live-section live-source-history"><div><header><h2>来源</h2></header>
      <dl className="reference-kv"><div><dt>宿主</dt><dd>Device Host · {status?.name ?? '等待连接'}</dd></div>
        <div><dt>broker</dt><dd>{status?.broker ?? '等待连接'}</dd></div>
        <div><dt>标识空间</dt><dd><code>/Real/…</code> · 与仿真设备分离</dd></div>
        <div><dt>布局版本</dt><dd><code>{status?.layout ?? '未配置'}</code></dd></div>
        <div><dt>历史来源</dt><dd>持续实况录制 · {status?.recording.status === 'recording' ? '正在录制' :
          status?.recording.status === 'disabled' ? '未开启' : status?.recording.status === 'incomplete' ? '录制已暂停' : '等待连接'}</dd></div>
        <div><dt>保留时长</dt><dd>{status ? `${status.recording.retentionDays} 天 · UTC 按天分段` : '等待连接'}</dd></div>
      </dl></div><div><header><h2>历史</h2></header>
      <p className="live-muted">每分钟聚合最近 1 小时的实际测量。过期读数显示为灰色，历史可跨天和网关重启查询。</p>
      {command && <div className="live-command"><code>$ {command}</code><button onClick={() => {
        void navigator.clipboard.writeText(command).then(() => setCopied(command)).catch(() => setCopied('复制失败，请手动选择命令'));
      }}>{copied === command ? '已复制' : '复制'}</button></div>}
      {copied && copied !== command && <span role="status">{copied}</span>}
      {status?.recording.id ? <Link to="/replay/$recordingId" params={{ recordingId: status.recording.id }}
        className="reference-link">回放实况录制 →</Link> : <Link to="/recordings" className="reference-link">查看录制 →</Link>}
    </div></section>
  </main>;
}
