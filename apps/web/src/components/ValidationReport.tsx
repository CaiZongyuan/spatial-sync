import type { ValidationReport as Report } from '../lib/library';

export function ValidationStatus({ status }: { status: string }) {
  return <span className="scene-note">{status === 'stale' ? '报告过期 · 工具版本已变化' : status === 'missing' ? '报告缺失' : '已记录'}</span>;
}

export function ValidationReport({ report }: { report: Report | null }) {
  if (!report) return <p className="scene-panel-body scene-failure">缺少验证报告。</p>;
  return <div className="scene-validation">
    {['structural', 'physics'].map(tier => {
      const checks = report.checks.filter(check => check.tier === tier || tier === 'structural' && check.tier === 'structure');
      return <div key={tier}><h3>{tier === 'structural' ? '结构检查' : '物理检查'}</h3>
        {tier === 'physics' && report.physics?.status === 'skipped' && <p className="scene-note">物理检查已跳过：{report.physics.reason}</p>}
        {tier === 'physics' && report.physics?.retainedFrom && <p className="scene-note">保留上次物理结论{report.physics.retainedFrom.ranAt ? `（${new Date(report.physics.retainedFrom.ranAt).toLocaleString()}）` : ''}。</p>}
        {checks.length ? checks.map((check, index) => <div key={`${check.id}:${check.entityKey ?? ''}:${check.location ?? ''}:${index}`} className={`scene-check is-${check.status}`}>
          <strong>{check.id}<span>{check.status === 'pass' ? '通过' : check.status === 'warn' ? '警告' : '失败'}</span></strong>
          {check.entityKey && <code>{check.entityKey}</code>}{check.location && <p>示教位置 <code>{check.location}</code></p>}<p>{check.message}</p>
        </div>) : <p className="scene-note">未记录此类检查。</p>}
        {tier === 'physics' && report.physics?.status === 'completed' && report.physics.ranAt && <small>Newton CPU · 执行于 {new Date(report.physics.ranAt).toLocaleString()}</small>}
      </div>;
    })}
    <small>{report.ranAt ? `报告记录于 ${new Date(report.ranAt).toLocaleString()}` : '未记录报告时间'}</small>
  </div>;
}
