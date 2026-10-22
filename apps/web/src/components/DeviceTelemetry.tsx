import { useCallback, useEffect, useId, useMemo, useState, useSyncExternalStore } from 'react';
import { request } from '../lib/api';
import type { Catalog, EntityBinding } from '../lib/protocol';
import { alertGuides, elapsed, reading, sparkline, type AlertEpisode, type DeclaredRule, type HistoryPoint } from '../lib/live';
import type { SessionClient } from '../lib/stream/client';
import { useEntity } from '../lib/info/hooks';
import { AlertAcknowledgement, Severity } from './Alerts';

export function useDeviceReadouts(client: SessionClient, keys: string[]) {
  const signature = JSON.stringify(keys);
  const owned = useMemo(() => JSON.parse(signature) as string[], [signature]);
  const subscribe = useCallback((listener: () => void) => {
    const stops = owned.map(key => client.info.subscribeEntity(key, listener));
    return () => { for (const stop of stops) stop(); };
  }, [client, owned]);
  const snapshot = useMemo(() => {
    let current = owned.map(key => client.info.getEntity(key));
    return () => {
      const next = owned.map(key => client.info.getEntity(key));
      if (next.some((value, index) => value !== current[index])) current = next;
      return current;
    };
  }, [client, owned]);
  return useSyncExternalStore(subscribe, snapshot);
}

export function AlertEpisodes({ episodes, error, loaded, empty }: {
  episodes: AlertEpisode[]; error?: string; loaded: boolean; empty: string;
}) {
  return error ? <p role="status" className="platform-error">告警暂不可用 · {error} · 自动重试中</p> :
    episodes.length ? <div className="live-table-scroll"><table className="live-table" data-testid="live-alerts">
      <thead><tr><th>严重度</th><th>设备 / 规则</th><th className="live-number">读数与开始</th><th>状态 / 确认</th></tr></thead>
      <tbody>{episodes.map(alert => <tr key={alert.alertId} data-alert-id={alert.alertId}>
        <td><Severity severity={alert.severity} /></td><td><code>{alert.entityKey}</code><span>{alert.message}</span>
          <small>{alert.rule}</small>{alert.hint && <small>{alert.hint}</small>}</td>
        <td className="live-number">{reading(alert.value)}<small>自 {reading(alert.since)} s 起</small></td>
        <td><span className="live-alert-state">{alert.active ? alert.acknowledgement ? '活动 · 已确认' : '活动 · 待确认' :
          alert.closedContext ? '上下文结束' : '已恢复'}</span><AlertAcknowledgement alert={alert} /></td>
      </tr>)}</tbody></table></div> : loaded ? <p className="live-empty">{empty}</p> : null;
}

export function Trend({ sessionId, entityKey, property, stale, rules, unit }: { sessionId: string; entityKey: string; property: string; stale: boolean; rules: DeclaredRule[]; unit?: string }) {
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
        const query = new URLSearchParams({ key: entityKey, property, since: '1h', every: '1m', agg: 'mean' });
        if (!entityKey.startsWith('/Real/')) query.set('sessionId', sessionId);
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
  if (!history.points) return <div className="live-loading" role="status" aria-busy="true"><span>正在读取历史…</span><div className="live-skeleton live-trend-skeleton" aria-hidden="true"><i /><i /></div></div>;
  if (!chart) return <div className="trend-status">暂无数值历史</div>;
  const guides = alertGuides(rules, property, chart);
  return <div className={`live-trend ${stale ? 'is-stale' : ''}`} data-testid={`trend-${entityKey}-${property}`}>
    <svg viewBox="0 0 280 64" role="img" aria-label={`${property} 实际历史 ${elapsed(chart.end - chart.start)}，最小 ${reading(chart.low)}，最大 ${reading(chart.high)}`}>
      <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".18" /><stop offset="100%" stopColor="currentColor" stopOpacity="0" /></linearGradient></defs>
      <path className="trend-grid" d="M6 7H230 M6 28H230 M6 49H230" />
      {chart.count > 1 && <path d={`${chart.path} L${chart.last[0]},49 L6,49 Z`} fill={`url(#${id})`} />}
      {guides.thresholds.filter(guide => guide.y !== undefined).map(guide => <line key={guide.rule} className="trend-alert-guide" x1="6" x2="230" y1={guide.y} y2={guide.y}>
        <title>{guide.rule} 告警阈值 {guide.operator} {reading(guide.value)} {unit}</title></line>)}
      <path className="trend-line" d={chart.path} />
      <circle cx={chart.last[0]} cy={chart.last[1]} r="2.8" fill="currentColor" />
      <text x="236" y="12">{reading(chart.high)}</text>{chart.low !== chart.high && <text x="236" y="50">{reading(chart.low)}</text>}
      {chart.start === chart.end ? <text x="118" y="62" textAnchor="middle">0 秒</text> : <><text x="6" y="62">0</text><text x="230" y="62" textAnchor="end">{elapsed(chart.end - chart.start)}</text></>}
    </svg>
    <span>{chart.count} 个聚合点 · 实际跨度 {elapsed(chart.end - chart.start)}</span>
    {guides.thresholds.map(guide => <span className="trend-guide-note" key={guide.rule}>{guide.rule} · 告警阈值 {guide.operator} {reading(guide.value)} {unit}{guide.y === undefined ? '（超出当前数据范围）' : ' · 虚线'}</span>)}
    {guides.unplotted.length > 0 && <span className="trend-guide-note">组合或动态规则：{guides.unplotted.join(' · ')}（历史阈值未重建）</span>}
  </div>;
}

export function DeviceReadings({ client, catalog, binding, rules, trends = true }: { client: SessionClient; catalog: Catalog; binding: EntityBinding; rules: DeclaredRule[]; trends?: boolean }) {
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
        <Trend sessionId={client.sessionId} entityKey={binding.key} property={name} unit={property.unit ?? undefined} stale={stale} rules={rules.filter(rule => rule.key === binding.key)} />}
    </div>;
  })}</div>;
}

export function DeclaredRulesTable({ rules, error, loading, retry }: { rules: DeclaredRule[]; error?: string; loading: boolean; retry: () => void }) {
  return <div className="declared-rules"><h3 className="eyebrow">已声明的告警规则 · 当前声明</h3>
    {error ? <p role="status" className="live-muted">规则暂不可用 · {error} <button className="reference-link" onClick={retry}>重试</button></p> : loading ?
      <div className="live-loading" role="status" aria-busy="true"><span>正在读取作者声明…</span><div className="live-skeleton live-rules-skeleton" aria-hidden="true">{Array.from({ length: 12 }, (_, index) => <i key={index} />)}</div></div> : rules.length ?
      <div className="live-table-scroll"><table className="live-table"><thead><tr><th>设备 / 规则</th><th>条件</th><th className="live-number">持续</th><th>严重度</th></tr></thead><tbody>{rules.map(rule =>
        <tr key={`${rule.key}-${rule.name}`}><td><code>{rule.key}</code><small>{rule.name}</small></td><td><span>{rule.message}</span><details><summary>查看条件</summary><code>{JSON.stringify(rule.when)}</code>{rule.hint && <p>{rule.hint}</p>}</details></td><td className="live-number">{reading(rule.forSeconds)} s</td><td><Severity severity={rule.severity} /></td></tr>)}</tbody></table></div>
      : <p className="live-muted">当前未声明告警规则。</p>}
  </div>;
}

