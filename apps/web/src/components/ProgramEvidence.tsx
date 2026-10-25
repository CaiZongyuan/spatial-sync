import type { CandidateIdentity, ProgramExecution } from '../lib/evaluations';
import '../program-evidence.css';

export function ProgramEvidence({ identity, execution }: {
  identity?: CandidateIdentity | null; execution?: ProgramExecution | null;
}) {
  if (identity?.kind !== 'program' && !execution) return null;
  const id = execution?.program ?? identity?.id;
  const hashes = [
    ['内容', execution?.programContentHash ?? identity?.contentHash],
    ['代码', execution?.codeHash ?? identity?.codeHash],
    ['配置', execution?.configHash ?? identity?.configHash],
  ];
  return <details className="program-evidence" data-testid="program-evidence">
    <summary>{execution ? '脚本原生执行回执' : '脚本代码快照'}</summary>
    <code>{id ?? '脚本身份未知'}</code>
    <dl>{hashes.map(([label, hash]) => <div key={label}><dt>{label}</dt>
      <dd><code title={hash ?? undefined}>{hash ? hash.slice(0, 16) : '未知'}</code></dd></div>)}</dl>
    {execution ? <p>本回合动作启动 {execution.actionStarts} · 控制 tick {execution.callbacks.tick ?? '未知'} · 回调错误 {execution.callbackErrors}</p>
      : <p>固定的代码与配置；任务结果见平台评分和回放。</p>}
  </details>;
}
