import type { NodeGPU } from '../lib/jobs';
import { localTime, percentage } from '../lib/training-presentation';

export function GPUHistory({ gpu, now = Date.now() / 1000 }: { gpu: NodeGPU; now?: number }) {
  const history = gpu.history;
  const window = history?.windowSeconds ?? 3600;
  const samples = history?.samples.filter(sample => sample.at >= now - window && sample.at <= now) ?? [];
  const segments: { at: number; utilization: number }[][] = [];
  let current: { at: number; utilization: number }[] | undefined;
  for (const sample of samples) {
    if (sample.utilization == null) { current = undefined; continue; }
    if (!current || sample.at - current[current.length - 1].at > (history?.sampleIntervalSeconds ?? 10) * 2) {
      current = []; segments.push(current);
    }
    current.push({ at: sample.at, utilization: sample.utilization });
  }
  const x = (at: number) => 28 + ((at - (now - window)) / window) * 548;
  const y = (utilization: number) => 112 - utilization * .9;
  const recorded = samples.filter(sample => sample.utilization != null).length;
  const covered = history?.startedAt == null ? 0 : Math.min(window, Math.max(0, now - history.startedAt));
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
    <p className="resource-note">{covered < window ? '一小时前的完整历史尚未积累；较早时段留空。' : '历史仅保留最近一小时。'}
      {samples.some(sample => sample.utilization == null) && ' 存在节点未提供的采样。'}</p>
  </div>;
}
