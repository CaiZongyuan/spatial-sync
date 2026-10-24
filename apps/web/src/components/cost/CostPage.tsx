import { useContext, useEffect, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { acknowledgeCostAnomaly, CostAnomalyContext, costFilters, costRanges, useCostDashboard, type CostFilters, type CostTotals } from '../../lib/cost';
import { dollars } from '../../lib/model-resources';
import { localTime, numberOrUnknown } from '../../lib/training-presentation';
import { CostBreakdownTable, GpuChart, LlmCharts } from './CostCharts';
import './cost.css';

const labels = { '1h': '最近一小时', '1d': '最近一天', '7d': '近 7 天', '30d': '近 30 天', custom: '自定义' };
const preferenceKey = 'lab.cost.filters';
const usage = (value: number | null) => value == null ? '未接入' : numberOrUnknown(value, 0);
function dateInput(iso: string | undefined) {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isFinite(date.getTime()) ? new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
}
function SummaryCard({ title, values }: { title: string; values: CostTotals }) {
  return <section className="cost-summary-card" aria-label={`${title} 成本摘要`}><h2>{title}</h2><dl>
    <div><dt>Token</dt><dd>{usage(values.tokens)}</dd></div><div><dt>请求</dt><dd>{usage(values.requests)}</dd></div>
    <div><dt>LLM 成本（估算）</dt><dd>{values.llmUsd == null ? '未接入' : dollars(values.llmUsd)}</dd></div>
    <div><dt>GPU 小时</dt><dd>{numberOrUnknown(values.gpuHours, 6)}</dd></div>
  </dl></section>;
}

export function CostPage({ filters, onFiltersChange }: { filters: CostFilters; onFiltersChange: (filters: CostFilters) => void }) {
  const { data, error, refresh } = useCostDashboard(filters);
  const anomalies = useContext(CostAnomalyContext);
  const restored = useRef(false);
  const [reading, setReading] = useState(false), [readError, setReadError] = useState<string>();
  useEffect(() => {
    if (!restored.current) {
      restored.current = true;
      if (!location.search) {
        try {
          const saved = localStorage.getItem(preferenceKey);
          if (saved) { onFiltersChange(costFilters(JSON.parse(saved))); return; }
        } catch { /* An invalid optional preference uses URL defaults. */ }
      }
    }
    try { localStorage.setItem(preferenceKey, JSON.stringify(filters)); } catch { /* Local preferences are optional. */ }
  }, [filters, onFiltersChange]);
  const change = (patch: Partial<CostFilters>) => onFiltersChange({ ...filters, ...patch });
  const selectRange = (range: CostFilters['range']) => {
    change(range === 'custom' ? { range, start: filters.start ?? new Date(Date.now() - 86400000).toISOString(), end: filters.end ?? new Date().toISOString() }
      : { range, start: undefined, end: undefined });
  };
  const markRead = async (ids: string[]) => {
    if (reading) return;
    setReading(true); setReadError(undefined);
    try { await Promise.all(ids.map(acknowledgeCostAnomaly)); }
    catch (failure) { setReadError((failure as Error).message); }
    finally { setReading(false); }
  };
  const choices = (key: 'agent' | 'model' | 'node' | 'goal' | 'plan') => [...new Set([
    ...(data?.summary.choices[key] ?? []), ...(key === 'model' ? data?.models.map(model => model.ref) ?? [] : []),
    ...(key === 'node' ? data?.connectedNodes.map(node => node.id) ?? [] : []), ...(filters[key] ? [filters[key]!] : []),
  ])];
  const total = data?.summary.totals;
  return <main className="cost-page" data-testid="cost-page">
    <header className="cost-heading"><div><p className="eyebrow">COST & USAGE</p><h1>成本与统计</h1><p>GPU 用量与人维护的价格估算</p></div>
      <button onClick={refresh}>刷新成本</button></header>
    <div className="cost-filters" aria-label="成本筛选">
      {([['agent', 'Agent'], ['model', '模型'], ['node', '节点'], ['goal', 'RSI 目标'], ['plan', '计划']] as const).map(([key, label]) =>
        <label key={key}>{label}<select aria-label={`成本${label}`} value={filters[key] ?? ''} onChange={event => change({ [key]: event.target.value || undefined })}>
          <option value="">全部{label}</option>{choices(key).map(value => <option key={value} value={value}>{value}</option>)}
        </select></label>)}
      <div className="cost-range" role="group" aria-label="成本时间范围">{costRanges.map(range =>
        <button key={range} aria-pressed={filters.range === range} onClick={() => selectRange(range)}>{labels[range]}</button>)}</div>
      {filters.range === 'custom' && <div className="cost-custom-dates">{([['start', '开始'], ['end', '结束']] as const).map(([key, label]) =>
        <label key={key}>{label}（本地时间）<input type="datetime-local" aria-label={`成本${label}时间`} value={dateInput(filters[key])}
          onChange={event => change({ [key]: event.target.value ? new Date(event.target.value).toISOString() : undefined })} /></label>)}</div>}
    </div>
    {error && <p role="alert">{error}{data && ' 下方显示上次成功读取的账本。'} <button onClick={refresh}>重试</button></p>}
    {!data && !error && <p role="status" aria-busy="true">正在读取成本账本…</p>}
    {data && total && <>
      <section className="cost-selected-total" aria-label="当前范围成本摘要"><div><span>{labels[filters.range]}</span>
        <span title={`${new Date(data.summary.window.start * 1000).toISOString()} — ${new Date(data.summary.window.end * 1000).toISOString()}`}>
          {localTime(data.summary.window.start)} — {localTime(data.summary.window.end)} <small>（时间桶按 UTC）</small></span></div>
        <strong data-testid="cost-selected-gpu">{numberOrUnknown(total.gpuHours, 6)} <small>GPU 小时</small></strong>
        <span>GPU 估算 {dollars(total.gpuUsd)}</span>
        {total.unpricedGpuSeconds > 0 && <small className="cost-price-unknown">* 未定价 {numberOrUnknown(total.unpricedGpuSeconds, 3)} GPU 秒；已定价部分 {dollars(total.knownGpuUsd)}</small>}
      </section>
      <div className="cost-summary-grid"><SummaryCard title="今日 · UTC" values={data.summary.cards.today} /><SummaryCard title="近 7 天" values={data.summary.cards['7d']} /><SummaryCard title="累计" values={data.summary.cards.all} /></div>
      <div className="cost-charts-grid"><LlmCharts buckets={data.buckets} /><GpuChart breakdown={data.nodes} connectedNodes={data.connectedNodes} /></div>
      <div className="cost-breakdown-grid"><CostBreakdownTable title="按 RSI 目标分摊" data={data.goals} /><CostBreakdownTable title="按计划分摊" data={data.plans} /></div>
      <section className="panel cost-anomalies"><header><div><h2>成本异常</h2><span>{anomalies.data?.unreadCount ?? '—'} 条未读</span></div>
        {anomalies.data?.canAcknowledge && <button disabled={reading || !anomalies.data.unreadCount}
          onClick={() => void markRead(anomalies.data!.anomalies.filter(row => !row.read).map(row => row.id))}>全部标为已读</button>}</header>
        {anomalies.error && <p role="alert">{anomalies.error}</p>}{readError && <p role="alert">{readError}</p>}
        {anomalies.data && !anomalies.data.anomalies.length && <p className="cost-chart-note">暂无已记录的作业失败或评估超时。</p>}
        <ul>{anomalies.data?.anomalies.map(row => <li className={row.read ? 'is-read' : ''} key={row.id}>
          <span className={`cost-severity is-${row.severity}`}>{row.severity === 'error' ? '错误' : '警告'}</span>
          <div><strong>{row.title}</strong><p>{row.detail}</p><small>{localTime(row.at)}{row.plan && ` · ${row.plan}`}</small>
            {row.jobId && <Link to="/train/jobs/$id" params={{ id: row.jobId }}>{row.jobId}</Link>}</div>
          {row.read ? <span>已读</span> : anomalies.data?.canAcknowledge && <button disabled={reading} onClick={() => void markRead([row.id])}>标为已读</button>}
        </li>)}</ul>
      </section>
      <footer className="cost-footer"><p>{total.estimatedGpuSeconds > 0 || total.historicalGpuSeconds > 0
        ? 'GPU 账本含保守计数或历史累计，已上报量与估计量分列；金额按价格表估算。'
        : 'Token 与 GPU 小时为平台精确记录，金额按价格表估算。'}</p>
        <p>LLM 用量尚未接入，未显示的请求与 Token 保留未知。GPU 已上报 {numberOrUnknown(total.reportedGpuSeconds, 3)} 秒；失联或尚未确认的保守计数 {numberOrUnknown(total.estimatedGpuSeconds, 3)} 秒。</p>
        {total.historicalGpuSeconds > 0 && <p>含历史累计 {numberOrUnknown(total.historicalGpuSeconds, 3)} 秒，缺少原始观测时点与价格版本。</p>}
        <p>时间桶按 UTC，GPU 增量按相邻观测区间分摊；已知排队区间不计入。* 表示缺少价格，金额保留未知，已定价部分单独列出。<Link to="/resources/models">价格与审计</Link></p>
      </footer>
    </>}
  </main>;
}
