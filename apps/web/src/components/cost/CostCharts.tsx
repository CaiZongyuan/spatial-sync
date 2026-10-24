import { Link } from '@tanstack/react-router';
import type { CostBreakdown, CostBucket } from '../../lib/cost';
import type { TrainingNode } from '../../lib/jobs';
import { dollars } from '../../lib/model-resources';
import { numberOrUnknown } from '../../lib/training-presentation';

const llmValue = (value: number | null) => value == null ? '未接入' : numberOrUnknown(value, 0);
export function CostSeriesTable({ buckets, title, fields }: { buckets: CostBucket[]; title: string;
  fields: { key: keyof CostBucket; label: string; money?: boolean }[] }) {
  return <details className="cost-chart-data"><summary>查看数据表（{buckets.length} 个时间桶）</summary>
    <div className="cost-table-scroll" role="region" aria-label={`${title} 数据区域`} tabIndex={0}>
      <table aria-label={`${title} 数据表`}><thead><tr><th scope="col">时间桶（UTC）</th>{fields.map(field => <th scope="col" key={field.key}>{field.label}</th>)}</tr></thead>
        <tbody>{buckets.map(bucket => <tr key={bucket.start}><th scope="row">{new Date(bucket.start * 1000).toISOString()}<small>至 {new Date(bucket.end * 1000).toISOString()}</small></th>
          {fields.map(field => <td key={field.key}>{field.money ? dollars(bucket[field.key] as number | null) : llmValue(bucket[field.key] as number | null)}</td>)}
        </tr>)}</tbody></table>
    </div></details>;
}

export function LlmCharts({ buckets }: { buckets: CostBucket[] }) {
  return <>
    <section className="panel cost-chart"><header><h2>请求 · 按 Agent</h2><span className="cost-chart-legend"><i />请求数</span></header>
      <div className="cost-ratio-band" role="img" aria-label="独立成功率带：尚无已上报的 LLM 请求，成功率未知"><span>成功率</span><small>暂无已上报请求</small></div>
      <div className="cost-empty-plot" role="img" aria-label="Agent 请求堆叠柱：LLM 用量尚未接入"><p>LLM 请求尚未接入</p><small>等待 Agent 运行器上报真实请求与结果。</small></div>
      <CostSeriesTable title="请求" buckets={buckets} fields={[{ key: 'requests', label: '请求' }, { key: 'failures', label: '失败' }, { key: 'successRate', label: '成功率' }]} />
    </section>
    <section className="panel cost-chart"><header><h2>Token 构成</h2><span className="cost-chart-legend"><i />输入 / 输出 / 缓存读取</span></header>
      <div className="cost-ratio-band" role="img" aria-label="独立缓存命中率带：尚无已上报的 Token，命中率未知"><span>缓存命中率</span><small>暂无已上报 Token</small></div>
      <div className="cost-empty-plot" role="img" aria-label="Token 堆叠柱：LLM 用量尚未接入"><p>Token 用量尚未接入</p><small>输入、输出与缓存读取保留未知。</small></div>
      <CostSeriesTable title="Token" buckets={buckets} fields={[{ key: 'inputTokens', label: '输入' }, { key: 'outputTokens', label: '输出' }, { key: 'cacheReadTokens', label: '缓存读取' }, { key: 'cacheHitRate', label: '缓存命中率' }]} />
    </section>
    <section className="panel cost-chart"><header><h2>LLM 成本变化</h2><span className="cost-chart-legend">USD · 估算</span></header>
      <div className="cost-empty-plot is-tall" role="img" aria-label="LLM 成本面积折线：没有真实 Token 记录，峰值和末值未知"><p>尚无可估算的 LLM 用量</p><small>峰值 * · 末值 *</small></div>
      <CostSeriesTable title="LLM 成本" buckets={buckets} fields={[{ key: 'llmUsd', label: 'LLM 估算', money: true }]} />
    </section>
  </>;
}

export function GpuChart({ breakdown, connectedNodes }: { breakdown: CostBreakdown; connectedNodes: TrainingNode[] }) {
  const known = new Set(breakdown.rows.map(row => row.key));
  const groups = [...breakdown.rows, ...connectedNodes.filter(node => !known.has(node.id)).map(node => ({ key: node.id, label: node.id, jobs: [] }))];
  const largest = Math.max(0, ...breakdown.rows.flatMap(row => row.jobs.map(job => job.gpuHours)));
  return <section className="panel cost-chart"><header><h2>GPU 小时 · 按节点 / 作业</h2><span className="cost-chart-legend"><i />运行中</span></header>
    {!groups.length && <div className="cost-empty-gpu"><strong>0 GPU 小时</strong><span>未接入计算节点</span></div>}
    <div role="group" aria-label={`GPU 用量横向条，${groups.length} 个节点；精确数值在下方数据表`} className="cost-gpu-bars">
      {groups.map(group => <div className="cost-gpu-group" key={group.key ?? 'none'}><strong>{group.label}</strong>
        {!group.jobs.length && <p>0 GPU 小时 · 尚无作业用量</p>}
        {group.jobs.map(job => <div className={`cost-gpu-row${job.status === 'running' ? ' is-running' : ''}`} key={job.jobId}>
          <Link to="/train/jobs/$id" params={{ id: job.jobId }}>{job.jobId}</Link>
          <svg viewBox="0 0 100 9" preserveAspectRatio="none" role="img" aria-label={`${job.jobId} GPU 小时 ${numberOrUnknown(job.gpuHours, 6)}`}>
            <title>{group.label} · {job.jobId} · {numberOrUnknown(job.gpuHours, 6)} GPU 小时</title>
            <rect x="0" y="0" width="100" height="9" className="cost-gpu-track" />
            <rect x="0" y="0" width={largest ? job.gpuHours / largest * 100 : 0} height="9" className="cost-gpu-value" />
          </svg>
          <span>{numberOrUnknown(job.gpuHours, 6)} h</span>
        </div>)}
      </div>)}
    </div>
    <details className="cost-chart-data"><summary>查看 GPU 数据表</summary><div className="cost-table-scroll" role="region" aria-label="GPU 数据区域" tabIndex={0}>
      <table aria-label="GPU 用量数据表"><thead><tr><th scope="col">节点</th><th scope="col">作业 / 状态</th><th scope="col">GPU 秒</th><th scope="col">已上报秒</th><th scope="col">估计秒</th><th scope="col">GPU 估算</th><th scope="col">价格版本</th></tr></thead>
        <tbody>{breakdown.rows.flatMap(row => row.jobs.map(job => <tr key={job.jobId}><th scope="row">{row.label}</th><td>{job.jobId}<small>{job.status}</small></td>
          <td>{numberOrUnknown(job.gpuSeconds, 3)}</td><td>{numberOrUnknown(job.reportedGpuSeconds, 3)}</td><td>{numberOrUnknown(job.estimatedGpuSeconds, 3)}</td>
          <td>{dollars(job.gpuUsd)}{job.unpricedGpuSeconds > 0 && <small>未定价 {numberOrUnknown(job.unpricedGpuSeconds, 3)} 秒</small>}</td>
          <td>{job.priceRevisions.join(', ') || '未知'}</td></tr>))}</tbody></table>
    </div></details>
    <p className="cost-chart-note">价格来自入账时的人维护版本；<Link to="/resources/models">查看价格表</Link>。显存与用量取节点报告。</p>
  </section>;
}

export function CostBreakdownTable({ data, title }: { data: CostBreakdown; title: string }) {
  return <section className="panel cost-breakdown"><header><h2>{title}</h2><span>当前范围</span></header>
    <div className="cost-table-scroll" role="region" aria-label={`${title} 数据区域`} tabIndex={0}><table aria-label={title}>
      <thead><tr><th scope="col">{data.by === 'goal' ? 'RSI 目标' : '计划 / 固定修订'}</th><th scope="col">请求</th><th scope="col">Token</th><th scope="col">LLM 估算</th><th scope="col">GPU 小时</th><th scope="col">GPU 占比</th></tr></thead>
      <tbody>{data.rows.map(row => <tr key={row.key ?? 'none'}><th scope="row">{row.label}</th><td>{llmValue(row.requests)}</td><td>{llmValue(row.tokens)}</td>
        <td>{dollars(row.llmUsd)}</td><td>{numberOrUnknown(row.gpuHours, 6)}</td><td>{row.gpuShare == null ? '—' : `${numberOrUnknown(row.gpuShare * 100, 1)}%`}</td></tr>)}</tbody>
    </table></div>{!data.rows.length && <p className="cost-chart-note">当前范围与筛选下没有用量。</p>}
  </section>;
}
