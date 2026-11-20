import type { MotionFeasibility } from '../lib/motion';

const sourceLabels: Record<string, string> = {
  source_landmark: '源姿态关键点', planted_support: '站立支撑目标',
  asset_nominal: '素材名义位姿', authored_keyframe: '关键帧目标',
};

function measurement(value: number | undefined, unit = '') {
  return typeof value === 'number' && Number.isFinite(value) ? `${value}${unit ? ` ${unit}` : ''}` : '未记录';
}

export function ReferenceFeasibility({ report }: { report: MotionFeasibility }) {
  const keypoints = Object.keys({ ...report.keypointErrorM, ...report.keypointTargets });
  const joints = Object.entries(report.maxJointSpeed ?? {});
  const bounds = report.bounds;
  return <section aria-label="参考动作可行性">
    <h3>可行性</h3>
    <p>足滑 {measurement(report.maxFootSlipM, 'm')} · 支撑裕量 {measurement(report.minSupportMarginM, 'm')}
      {' · '}关节限位饱和 {measurement(report.jointLimitSaturation)}</p>
    <h4>关键点误差</h4>
    {keypoints.length ? <div className="asset-table-wrap"><table>
      <thead><tr><th scope="col">关键点</th><th scope="col">目标与来源</th><th scope="col">误差</th></tr></thead>
      <tbody>{keypoints.map(name => {
        const target = report.keypointTargets?.[name];
        const source = target?.source;
        return <tr key={name}><th scope="row">{name}</th>
          <td>{target?.path && <code>{target.path}</code>} {source ? sourceLabels[source] ?? source : '来源未记录'}</td>
          <td>{measurement(report.keypointErrorM?.[name], 'm')}</td></tr>;
      })}</tbody>
    </table></div> : <p>此报告未记录关键点误差。</p>}
    <h4>速度与上限</h4>
    <dl className="asset-kv">
      <div><dt>根线速度 / 上限</dt><dd>{measurement(report.maxRootSpeedMps, 'm/s')} / {measurement(bounds?.rootSpeedMps, 'm/s')}</dd></div>
      <div><dt>根角速度 / 上限</dt><dd>{measurement(report.maxRootAngularSpeedRadps, 'rad/s')} / {measurement(bounds?.rootAngularSpeedRadps, 'rad/s')}</dd></div>
      <div><dt>旋转关节速度上限</dt><dd>{measurement(bounds?.jointSpeedRadps, 'rad/s')}</dd></div>
      <div><dt>移动关节速度上限</dt><dd>{measurement(bounds?.slideSpeedMps, 'm/s')}</dd></div>
    </dl>
    {joints.length > 0 && <details><summary>各关节峰值速度</summary><ul>{joints.map(([path, speed]) =>
      <li key={path}><code>{path}</code> · {measurement(speed, '关节原生单位/s')}</li>)}</ul></details>}
  </section>;
}
