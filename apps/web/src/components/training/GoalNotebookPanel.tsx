import { Link } from '@tanstack/react-router';
import { useContext, useEffect, useRef, useState } from 'react';
import { request } from '../../lib/api';
import { ApprovalInboxContext } from '../../lib/approvals';
import { getGoalLoop, listGoals, type GoalLoop, type HypothesisVerdict, type NotebookEntry } from '../../lib/notebooks';
import { LoopBudgetSummary } from './LoopBudgetSummary';
import './notebooks.css';

const verdicts = { confirmed: '达成预测', refuted: '预测被反驳', inconclusive: '证据不足' };
const percent = (value: number | null | undefined) => value == null ? '未知' : `${(value * 100).toFixed(1)}%`;
const decisions = { baseline: '首轮参照', accepted: '平台记录接受', rejected: '平台记录拒绝' };

function ScoreChart({ entries }: { entries: NotebookEntry[] }) {
  const points = entries.filter(entry => entry.score != null);
  const comparison = points.at(-1)?.comparison;
  const reference = comparison?.state === 'comparable' ? comparison.referenceScore : null;
  const target = entries.at(-1)?.hypotheses.find(hypothesis => ['success', 'successRate', 'score'].includes(hypothesis.definition.predict.metric));
  const declared = target?.definition.predict.to;
  const match = typeof declared === 'string' ? declared.match(/[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?/) : null;
  const targetValue = entries.at(-1)?.plan.targetSuccessRate ?? (typeof declared === 'number' ? declared : match ? Number(match[0]) : null);
  const groups = new Map<string, { index: number; entry: NotebookEntry }[]>();
  points.forEach((entry, index) => {
    if (!['baseline', 'comparable'].includes(entry.comparison.state)) return;
    const group = groups.get(entry.comparison.conditionsGroup) ?? [];
    group.push({ index, entry }); groups.set(entry.comparison.conditionsGroup, group);
  });
  const x = (index: number) => 60 + index / Math.max(1, points.length - 1) * 600;
  const y = (score: number) => 190 - score * 150;
  return <figure className="rsi-chart"><figcaption>平台评估成功率 · Wilson 95% 区间</figcaption>
    {!points.length ? <p className="rsi-note">暂无完整平台评估；失败和不完整记录仍列在实验记录中。</p> : <svg viewBox="0 0 720 245" role="img" aria-label="各轮成功率和95%置信区间，不同比较条件分别成组">
      {[0, .25, .5, .75, 1].map(value => <g key={value}><line className="rsi-grid-line" x1="60" x2="660" y1={y(value)} y2={y(value)} /><text x="50" y={y(value) + 4} textAnchor="end">{value * 100}%</text></g>)}
      {targetValue != null && targetValue >= 0 && targetValue <= 1 && <g><line className="rsi-target-line" x1="60" x2="660" y1={y(targetValue)} y2={y(targetValue)} /><text x="665" y={y(targetValue) + 4}>目标</text></g>}
      {reference != null && <g><line className="rsi-reference-line" x1="60" x2="660" y1={y(reference)} y2={y(reference)} /><text x="665" y={y(reference) - 6}>参照</text></g>}
      {[...groups.values()].map((group, index) => group.length > 1 && <polyline className={`rsi-series series-${index % 4}`} key={group[0].entry.comparison.conditionsGroup}
        points={group.map(point => `${x(point.index)},${y(point.entry.score!.score)}`).join(' ')} />)}
      {points.map((entry, index) => <g key={entry.entryId} className={`rsi-point is-${entry.reportedDecision}`}>
        <title>{entry.plan.title} · {entry.score!.successes}/{entry.score!.episodes} · {entry.comparison.state}</title>
        <line x1={x(index)} x2={x(index)} y1={y(entry.score!.ci95[0])} y2={y(entry.score!.ci95[1])} />
        {[0, 1].map(bound => <line key={bound} x1={x(index) - 5} x2={x(index) + 5} y1={y(entry.score!.ci95[bound])} y2={y(entry.score!.ci95[bound])} />)}
        <circle cx={x(index)} cy={y(entry.score!.score)} r="5" data-accepted={entry.reportedDecision === 'accepted' && entry.comparison.state === 'comparable'} />
        <text x={x(index)} y="216" textAnchor="middle">{entry.round ?? index + 1} · r{entry.plan.revision}</text>
      </g>)}
    </svg>}
    <p className="rsi-note">实心点表示平台记录接受且比较条件一致。条件不同或证据缺失时不连成提升曲线。统计接受仍要求 Wilson 下界严格高于参照点估计。</p>
  </figure>;
}

export function GoalNotebookPanel({ goal, onGoalChange }: { goal?: string; onGoalChange: (goal: string) => void }) {
  const [goals, setGoals] = useState<string[]>([]), [data, setData] = useState<GoalLoop>();
  const [error, setError] = useState<string>(), [retry, setRetry] = useState(0);
  const [editor, setEditor] = useState<{ entry: string; hypothesis: string; verdict: HypothesisVerdict; note: string; requestId: string }>();
  const [pending, setPending] = useState(false), [overrideError, setOverrideError] = useState<string>();
  const change = useRef(onGoalChange); change.current = onGoalChange;
  const approvals = useContext(ApprovalInboxContext);
  useEffect(() => {
    const controller = new AbortController();
    void listGoals(controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setGoals(result.goals); if (!goal && result.goals[0]) change.current(result.goals[0]);
    })
      .catch(failure => { if (!controller.signal.aborted) setError((failure as Error).message); });
    return () => controller.abort();
  }, [retry, goal]);
  useEffect(() => {
    setData(undefined); setError(undefined); setEditor(undefined);
    if (!goal) return;
    let disposed = false, active: AbortController | undefined;
    const refresh = async () => {
      if (active) return; const controller = new AbortController(); active = controller;
      try { const value = await getGoalLoop(goal, controller.signal); if (!disposed) { setData(value); setError(undefined); } }
      catch (failure) { if (!disposed) setError((failure as Error).message); }
      finally { active = undefined; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 5000);
    return () => { disposed = true; active?.abort(); clearInterval(timer); };
  }, [goal, retry]);
  const save = async () => {
    if (!goal || !editor || !editor.note.trim()) return;
    setPending(true); setOverrideError(undefined);
    try {
      await request(`/notebooks/${encodeURIComponent(goal)}/${encodeURIComponent(editor.entry)}`, { method: 'PATCH', body: JSON.stringify({
        hypothesisId: editor.hypothesis, verdict: editor.verdict, note: editor.note, requestId: editor.requestId,
      }) });
      setEditor(undefined); setRetry(value => value + 1);
    } catch (failure) { setOverrideError((failure as Error).message); }
    finally { setPending(false); }
  };
  const entries = data?.rounds ?? [], latest = entries.at(-1);
  return <section className="resource-panel rsi-panel" aria-label="具身层 RSI 看板" data-testid="goal-notebook-panel">
    <header><div><h2>RSI 看板与实验记录</h2><span className="resource-tag">具身层</span></div><button onClick={() => setRetry(value => value + 1)}>刷新目标记录</button></header>
    <div className="resource-panel-body"><label className="rsi-goal-choice">改进目标 <select aria-label="选择改进目标" value={goal ?? ''} onChange={event => onGoalChange(event.target.value)}>
      {!goal && <option value="">选择目标</option>}{goal && !goals.includes(goal) && <option value={goal}>{goal}</option>}
      {goals.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
      {error && <p className="resource-error" role="alert">目标记录暂不可用：{error}{data && '。保留最近一次读取。'}</p>}
      {!goal ? <p className="rsi-note">尚无目标记录。提交训练计划后可查看其默认目标，或在计划中声明 goalId。</p> : !data ? <p role="status">{error ? '请重试读取目标。' : '正在读取目标、预算与实验记录…'}</p> : <>
        <div className="rsi-pair"><article><span>本轮参照</span><strong>{percent(latest?.comparison.referenceScore)}</strong><small>{latest?.comparison.referenceEvaluationId ?? '尚无参照评估'}</small></article>
          <article><span>最新候选</span><strong>{percent(latest?.score?.score)}</strong><small>{latest?.subject ?? '等待平台评估'}</small></article>
          <article><span>候选接受</span><strong>{latest?.reportedDecision ? decisions[latest.reportedDecision] : '暂无完整决定'}</strong><small>与假设支持、人工部署分别记录</small></article>
          <article><span>部署审批</span><strong>{data.deployment.status === 'not_requested' ? '未申请部署' : data.deployment.status}</strong><small>本看板读取记录，不执行提升</small></article></div>
        <div className="rsi-main-grid"><ScoreChart entries={entries} /><LoopBudgetSummary budget={data.budget} /></div>
        {data.defaultProgram && <p className="rsi-note">固定评估场景默认程序：<code>{data.defaultProgram.program}</code> · {data.defaultProgram.scene ?? '场景未知'}。候选接受不会改变该绑定。</p>}
        <div className="resource-table-scroll"><table className="rsi-records" aria-label="实验记录表"><thead><tr><th>轮次 / 固定计划</th><th>平台成绩 / 95% CI</th><th>差距比例</th><th>假设支持</th><th>接受 / 部署 / 证据</th></tr></thead>
          <tbody>{entries.map(entry => <tr key={entry.entryId} data-entry-id={entry.entryId}>
            <td><strong>第 {entry.round} 轮 · r{entry.plan.revision}</strong><Link to="/plans/$planId" params={{ planId: entry.plan.id }}>{entry.plan.title}</Link><small>{entry.plan.task}</small><code>{entry.candidateVersion?.slice(0, 12) ?? '版本未知'}</code></td>
            <td>{entry.score ? <>{entry.score.successes}/{entry.score.episodes} · {percent(entry.score.score)}<small>[{percent(entry.score.ci95[0])}, {percent(entry.score.ci95[1])}]</small></> : <>无完整成绩<small>{entry.status} · {entry.errorCode ?? '未完成'}</small></>}<small>评估尝试 {entry.attemptedEpisodes ?? '未知'} · 已知作业 GPU {entry.knownJobGpuHours == null ? '未知' : entry.knownJobGpuHours.toFixed(3)}h</small></td>
            <td>{percent(entry.comparison.remainingGapReduction)}<small>{entry.comparison.gapReason === 'reference_at_ceiling' ? '参照已满分，分母为零' : entry.comparison.reason ?? (entry.comparison.remainingGapReduction == null ? '缺少可比参照' : '(新 − 旧) / (1 − 旧)')}</small>
              {entry.recordedComparison && <details><summary>历史比较快照（不作为本轮依据）</summary><small>{entry.recordedComparison.state} · 差距比例 {percent(entry.recordedComparison.remainingGapReduction)}</small><small>{entry.recordedComparison.referenceEvaluationId ?? '历史未记录参照'}</small></details>}</td>
            <td>{entry.hypotheses.map(hypothesis => <div className="rsi-hypothesis" key={hypothesis.definition.id}><strong>{hypothesis.definition.id} · {verdicts[hypothesis.effectiveVerdict]}</strong>
              <span>{hypothesis.definition.cause}</span><small>{hypothesis.definition.change}</small><small>自动：{verdicts[hypothesis.automatic.verdict]} · {hypothesis.automatic.metric} {hypothesis.automatic.observed ?? '未知'}</small>
              {hypothesis.overrides.map(audit => <small key={audit.requestId}>{audit.actor.name} · {new Date(audit.atMs).toLocaleString()}：{audit.note}</small>)}
              {approvals.inbox?.role === 'operator' && <button type="button" onClick={() => { setOverrideError(undefined); setEditor({ entry: entry.entryId, hypothesis: hypothesis.definition.id, verdict: hypothesis.effectiveVerdict, note: '', requestId: crypto.randomUUID() }); }}>人工改判 {hypothesis.definition.id}</button>}
            </div>)}</td>
            <td><strong>{entry.reportedDecision ? decisions[entry.reportedDecision] : '无接受决定'}</strong><small>{entry.deployment.status === 'not_requested' ? '未申请部署' : entry.deployment.status}</small><small>{entry.comparison.state === 'comparable' ? '比较条件一致' : entry.comparison.reason ?? '首轮参照'}</small>
              <Link to="/eval/$suiteId" params={{ suiteId: entry.suite }}>查看平台评估 →</Link>{entry.recordings.map(id => <Link key={id} to="/replay/$recordingId" params={{ recordingId: id }}>回放 {id.slice(-8)} →</Link>)}</td>
          </tr>)}</tbody></table>{!entries.length && <p className="rsi-note">暂无关联评估。实验记录在平台评估结束后写入，失败和无效结果也保留。</p>}</div>
        {editor && <form className="rsi-override" onSubmit={event => { event.preventDefault(); void save(); }}><h3>人工改判 · {editor.hypothesis}</h3>
          <label>有效判定 <select aria-label="人工假设判定" value={editor.verdict} onChange={event => setEditor({ ...editor, verdict: event.target.value as HypothesisVerdict })}>{Object.entries(verdicts).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>改判说明 <textarea aria-label="改判说明" required maxLength={4000} value={editor.note} onChange={event => setEditor({ ...editor, note: event.target.value })} /></label>
          <p className="rsi-note">保留自动判定和操作者记录；候选接受、部署审批保持原结果。</p>{overrideError && <p role="alert">{overrideError}</p>}
          <button type="submit" disabled={pending || !editor.note.trim()}>{pending ? '正在记录…' : '记录人工判定'}</button><button type="button" disabled={pending} onClick={() => setEditor(undefined)}>取消改判</button>
        </form>}
      </>}
    </div>
  </section>;
}
