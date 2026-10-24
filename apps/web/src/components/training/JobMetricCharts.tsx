import type { JobMetricSample } from '../../lib/jobs';

// A heartbeat may update elapsed time repeatedly during one training iteration.
// Keep its latest accepted observation without inventing another training update.
export function latestIterationSamples(history: JobMetricSample[]): JobMetricSample[] {
  const iterations = new Map<number, JobMetricSample>();
  for (const sample of history) {
    if (sample.iteration != null) iterations.set(sample.iteration, sample);
  }
  return [...iterations.values()].sort((a, b) => a.iteration! - b.iteration!);
}

const number = (value: number | null | undefined, digits = 3) => value == null ? '未知' : value.toFixed(digits);

function MetricPlot({ samples, field, label, percent = false }: {
  samples: JobMetricSample[]; field: 'meanReward' | 'trainSuccess'; label: string; percent?: boolean;
}) {
  const values = samples.map(sample => sample[field] == null ? null : sample[field]! * (percent ? 100 : 1));
  const known = values.filter((value): value is number => value != null);
  if (!known.length) return <div className="job-metric-chart"><h3>{label}</h3><p>暂无实际指标。</p></div>;
  const minimum = percent ? 0 : Math.min(...known), maximum = percent ? 100 : Math.max(...known);
  const span = maximum - minimum || 1;
  const first = samples[0].iteration!, last = samples.at(-1)!.iteration!;
  const x = (index: number) => 36 + (samples[index].iteration! - first) / (last - first || 1) * 520;
  const y = (value: number) => 128 - (value - minimum) / span * 100;
  let continuing = false;
  const path = values.map((value, index) => {
    if (value == null) { continuing = false; return ''; }
    const segment = `${continuing ? 'L' : 'M'}${x(index)},${y(value)}`;
    continuing = true;
    return segment;
  }).join(' ');
  return <div className="job-metric-chart"><h3>{label}</h3>
    <svg viewBox="0 0 600 164" role="img" aria-label={`${label}，横轴为训练 iteration；缺值处断开`}>
      <path className="job-metric-axis" d="M36,24 V128 H556" />
      <text x="2" y="30">{number(maximum, 1)}</text><text x="2" y="130">{number(minimum, 1)}</text>
      <text x="36" y="153">{first}</text><text x="550" y="153" textAnchor="end">{last} iteration</text>
      <path className="job-metric-line" d={path} />
      {values.map((value, index) => value == null ? null : <circle key={samples[index].iteration} cx={x(index)} cy={y(value)} r="2.5" />)}
    </svg></div>;
}

export function JobMetricCharts({ history = [] }: { history?: JobMetricSample[] }) {
  const samples = latestIterationSamples(history);
  return <section className="panel"><h2>标题指标曲线</h2>
    <p className="job-metric-note">来自节点已接受的心跳；同一 iteration 显示最后一次观测，未知值保持空缺。完整曲线见 TensorBoard。</p>
    <div className="job-metric-charts">
      <MetricPlot samples={samples} field="meanReward" label="Mean reward" />
      <MetricPlot samples={samples} field="trainSuccess" label="Train success (%)" percent />
    </div>
    <details><summary>心跳观测数据（保留最近 {history.length} 条）</summary>
      {history.length ? <div className="job-metric-table"><table><thead><tr>
        <th>接收时间</th><th>Iteration</th><th>Mean reward</th><th>Train success</th><th>显存 MB</th><th>报告状态 / 说明</th>
      </tr></thead><tbody>{history.map((sample, index) => <tr key={`${sample.receivedAt}:${index}`}>
        <td>{new Date(sample.receivedAt * 1000).toLocaleString()}</td><td>{sample.iteration ?? '未知'}</td>
        <td>{number(sample.meanReward)}</td><td>{sample.trainSuccess == null ? '未知' : `${number(sample.trainSuccess * 100, 1)}%`}</td>
        <td>{number(sample.memoryUsedMb, 1)}</td><td>{sample.reportedStatus}{sample.reason ? ` / ${sample.reason}` : ''}</td>
      </tr>)}</tbody></table></div> : <p>暂无已保留的心跳观测。</p>}
    </details>
  </section>;
}
