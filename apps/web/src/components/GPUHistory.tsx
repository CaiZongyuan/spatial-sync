import type { NodeGPU } from '../lib/jobs';
import { localTime, megabytes, percentage } from '../lib/training-presentation';

export function GPUHistory({ gpu, now = Date.now() / 1000 }: { gpu: NodeGPU; now?: number }) {
  const history = gpu.history;
  const window = history?.windowSeconds ?? 3600;
  const samples = history?.samples.filter(sample => sample.at >= now - window && sample.at <= now) ?? [];
  const segments: { at: number; utilization: number }[][] = [];
  let current: { at: number; utilization: number }[] | undefined;
  for (const sample of samples) {
    if (sample.utilization == null) { current = undefined; continue; }
    if (!current || sample.at - current[current.length - 1].at > (history?.sampleIntervalSeconds ?? 10) * 1.5) {
      current = []; segments.push(current);
    }
    current.push({ at: sample.at, utilization: sample.utilization });
  }
  const x = (at: number) => 28 + ((at - (now - window)) / window) * 548;
  const y = (utilization: number) => 112 - utilization * .9;
  const recorded = samples.filter(sample => sample.utilization != null).length;
  return <div className="gpu-history"><div className="gpu-history-heading"><span>GPU 使用率 · 过去 1 小时</span>
    <small>{recorded ? `${recorded} 个实测点` : '暂无可用历史'}</small></div>
    <svg viewBox="0 0 600 140" role="img" aria-label={`${gpu.name} 过去一小时 GPU 使用率，缺失采样留空`}>
      <title>仅连接连续的实际采样；缺失和未保留的时段留空</title>
      {[0, 50, 100].map(value => <g key={value}><line x1="28" x2="576" y1={y(value)} y2={y(value)} className="gpu-chart-grid" />
        <text x="2" y={y(value) + 3}>{value}</text></g>)}
      <text x="28" y="135">60 分钟前</text><text x="548" y="135">现在</text>
      {segments.map((segment, index) => <g key={index}>
        <polyline points={segment.map(sample => `${x(sample.at)},${y(sample.utilization)}`).join(' ')} className="gpu-chart-line" />
        {segment.map(sample => <circle key={sample.at} cx={x(sample.at)} cy={y(sample.utilization)} r="2" className="gpu-chart-point">
          <title>{localTime(sample.at)} · {percentage(sample.utilization)}</title></circle>)}
      </g>)}
    </svg>
    <p className="resource-note">窗口为最近一小时；未保留或未采样时留空。
      {samples.some(sample => sample.utilization == null) && ' 存在节点未提供的采样。'}</p>
    <details className="gpu-history-data">
      <summary>查看保留样本（{samples.length}）</summary>
      <div className="gpu-history-table" role="region" aria-label={`${gpu.name} 历史样本`} tabIndex={0}>
        <table aria-label={`${gpu.name} 保留样本`}>
          <thead><tr><th scope="col">记录时间</th><th scope="col">实际观测时间</th><th scope="col">GPU 使用率</th>
            <th scope="col">显存</th><th scope="col">原因</th></tr></thead>
          <tbody>{samples.map(sample => <tr key={sample.at}>
            <td>{localTime(sample.at)}</td><td>{localTime(sample.observedAt)}</td><td>{percentage(sample.utilization)}</td>
            <td>{megabytes(sample.memoryUsedMb)}</td><td>{sample.unavailableReason ?? '—'}</td>
          </tr>)}</tbody>
        </table>
        {!samples.length && <p className="resource-note">当前窗口没有保留样本。</p>}
      </div>
    </details>
  </div>;
}
