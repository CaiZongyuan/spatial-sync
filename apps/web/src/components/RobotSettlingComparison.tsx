import type { AssetDetail } from '../lib/asset-library';

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined;
}

const measure = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? String(value) : '未知';

function JointViolations({ value }: { value: unknown }) {
  const violations = record(value);
  if (!violations) return <>未知</>;
  const joints = Object.entries(violations);
  if (!joints.length) return <>无</>;
  return <details><summary>{joints.length} 个关节</summary><ul>{joints.map(([path, raw]) => {
    const entry = record(raw);
    const unit = entry?.unit === 'm' || entry?.unit === 'rad' ? entry.unit : '';
    return <li key={path}><code>{path}</code> · {measure(entry?.excess)} {unit}</li>;
  })}</ul></details>;
}

export function RobotSettlingComparison({ asset }: {
  asset: Pick<AssetDetail, 'kind' | 'validation' | 'validationStatus' | 'files'>;
}) {
  if (asset.kind !== 'robot') return null;
  const report = asset.validation;
  const check = report?.checks.find(check => check.id === 'P8');
  if (!check && !asset.files['source/robot.json']) return null;
  const measurements = check?.measurements;
  const newton = record(measurements?.newton), mujoco = record(measurements?.mujoco);

  return <section className="robot-settling" aria-label="双侧重力静置 P8">
    <h3>双侧重力静置 P8</h3>
    {asset.validationStatus === 'stale' && <p className="asset-small-note">报告已过期，以下为保留读数。</p>}
    {report?.physics?.retainedFrom && <p className="asset-small-note">保留上次物理检查的读数。</p>}
    {check && <p>{check.status === 'pass' ? '通过' : check.status === 'warn' ? '警告' : '失败'} · {check.message}</p>}
    {report?.physics?.status === 'skipped' && <p>物理检查已跳过{report.physics.reason && ` · ${report.physics.reason}`}</p>}
    {newton && mujoco ? <>
      <p className="asset-small-note">实测时长 {measure(measurements?.durationSeconds)} s · 步长 {measure(measurements?.physicsDt)} s</p>
      <div className="asset-table-wrap"><table>
        <thead><tr><th scope="col">静置末态测量</th><th scope="col">Newton</th><th scope="col">MuJoCo</th></tr></thead>
        <tbody>
          <tr><th scope="row">基座高度（m）</th><td>{measure(newton.baseHeightM)}</td><td>{measure(mujoco.baseHeightM)}</td></tr>
          <tr><th scope="row">基座倾角（°）</th><td>{measure(newton.baseTiltDeg)}</td><td>{measure(mujoco.baseTiltDeg)}</td></tr>
          <tr><th scope="row">最大自碰撞穿透（m）</th><td>{measure(newton.maxSelfPenetrationM)}</td><td>{measure(mujoco.maxSelfPenetrationM)}</td></tr>
          <tr><th scope="row">关节越界</th><td><JointViolations value={newton.jointLimitViolations} /></td>
            <td><JointViolations value={mujoco.jointLimitViolations} /></td></tr>
        </tbody>
      </table></div>
    </> : report?.physics?.status !== 'skipped' && <p>暂无双侧实测数据。</p>}
  </section>;
}
