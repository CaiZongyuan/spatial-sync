import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { useContext, useEffect, useState } from 'react';
import { request } from '../lib/api';
import { ApprovalInboxContext } from '../lib/approvals';
import type { AgentRun, HarnessVersion } from '../lib/agent-runs';
import { agentMean, agentName, type AgentLoop, type AgentNotebookEntry, type AgentReference, type AgentWorkflow } from '../lib/agent-rsi';
import type { HypothesisVerdict } from '../lib/notebooks';
import { ApprovalCard } from './ApprovalsPage';
import '../agent-training.css';

type Data = { workflow: AgentWorkflow; versions: HarnessVersion[]; runs: AgentRun[]; loop?: AgentLoop };
const usd = (value: number | null | undefined) => value == null ? '未知' : `$${value.toFixed(4)}`;
const date = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未知';
const observed = (value: number | null) => value == null ? '未知' : Number(value.toFixed(2));
const stateText: Record<string, string> = { submitted: '待审批', approved: '已批准', evaluating: '评估中', accepted: '训练接受', rejected: '已拒绝', inconclusive: '证据不足', contaminated: '污染停止', promoted: '已晋升', restore_blocked: '恢复受阻', waiting: '等待', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消' };

function AgentScoreChart({ entries, reference }: { entries: AgentNotebookEntry[]; reference?: AgentReference }) {
  const points = entries.filter(entry => entry.score !== null && entry.currentDisposition !== 'contaminated');
  const x = (index: number) => 64 + index / Math.max(1, points.length) * 590;
  const y = (score: number) => 190 - score * 1.5;
  return <figure className="agent-score-chart"><figcaption>Harness 均分 · 最低到最高须线</figcaption>
    {!points.length && !reference ? <p>尚无完整训练评分。</p> : <svg viewBox="0 0 720 238" role="img" aria-label="Agent 每版本均分与最低最高分，纵轴0到100">
      {[0, 25, 50, 75, 100].map(score => <g key={score}><line className="agent-grid" x1="64" x2="654" y1={y(score)} y2={y(score)} /><text x="52" y={y(score) + 4} textAnchor="end">{score}</text></g>)}
      {reference?.score != null && <g className="agent-reference"><line x1="64" x2="654" y1={y(reference.score)} y2={y(reference.score)} /><circle cx={x(0)} cy={y(reference.score)} r="5" /><text x={x(0)} y="218" textAnchor="middle">参照 v{reference.harnessVersion}</text></g>}
      {points.map((entry, index) => {
        const point = entry.score!, previous = points[index - 1];
        const comparable = entry.comparison.state === 'comparable';
        return <g key={entry.entryId} className={`agent-score-point is-${entry.reportedDecision}`} data-version={entry.candidateVersion}>
          <title>v{entry.candidateVersion} · 均分 {point.score} · 范围 {point.minimum}–{point.maximum} · {point.cases} 题 × {point.runs} 次 · {stateText[entry.currentDisposition === 'contaminated' ? 'contaminated' : entry.reportedDecision] ?? entry.reportedDecision}</title>
          {previous?.score && comparable && previous.comparison.state === 'comparable' && previous.comparison.conditionsGroup === entry.comparison.conditionsGroup && <line className="agent-series" x1={x(index)} x2={x(index + 1)} y1={y(previous.score.score)} y2={y(point.score)} />}
          <line x1={x(index + 1)} x2={x(index + 1)} y1={y(point.minimum)} y2={y(point.maximum)} />
          {[point.minimum, point.maximum].map((bound, ordinal) => <line key={ordinal} x1={x(index + 1) - 5} x2={x(index + 1) + 5} y1={y(bound)} y2={y(bound)} />)}
          <circle cx={x(index + 1)} cy={y(point.score)} r="6" data-accepted={entry.reportedDecision === 'accepted' && comparable} />
          <text x={x(index + 1)} y="218" textAnchor="middle">v{entry.candidateVersion}</text>
        </g>;
      })}
    </svg>}
    <p>实心表示条件一致的训练接受；拒绝或当前条件未知的版本为空心。须线来自实际题次范围。</p>
  </figure>;
}

function HarnessComposition({ agent, initialVersion, versions }: { agent: string; initialVersion: number; versions: HarnessVersion[] }) {
  const [selected, setSelected] = useState(initialVersion), [attempt, setAttempt] = useState(0);
  const [value, setValue] = useState<{ files: { path: string; content: string }[]; contentHash: string }>();
  const [error, setError] = useState<string>();
  useEffect(() => setSelected(initialVersion), [initialVersion, agent]);
  useEffect(() => {
    let disposed = false;
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 8000);
    setValue(undefined); setError(undefined);
    void request<{ files: { path: string; content: string }[]; contentHash: string }>(`/agents/${agentName(agent)}/harness/${selected}`, { signal: controller.signal })
      .then(result => { if (!disposed) setValue(result); })
      .catch(failure => { if (!disposed) setError(controller.signal.aborted ? '读取超时，请重试' : (failure as Error).message); })
      .finally(() => clearTimeout(timeout));
    return () => { disposed = true; controller.abort(); clearTimeout(timeout); };
  }, [agent, selected, attempt]);
  return <section className="resource-panel agent-composition" id="harness"><header><h2>Harness 组成</h2><select aria-label="查看 Harness 版本" value={selected} onChange={event => setSelected(Number(event.target.value))}>{versions.map(version => <option key={version.version} value={version.version}>v{version.version} · {stateText[version.decision] ?? version.decision}</option>)}</select></header>
    <div className="resource-panel-body">{error ? <p role="alert">{error} <button onClick={() => setAttempt(value => value + 1)}>重试组成</button></p> : !value ? <p role="status">正在读取固定文件…</p> : <>
      <code>{value.contentHash}</code>{value.files.map(file => <details key={file.path}><summary>{file.path}</summary><pre>{file.content.slice(0, 16000)}</pre>{file.content.length > 16000 && <small>仅显示前 16,000 字符；完整固定文件可通过 Harness 命令读取。</small>}</details>)}
    </>}</div>
  </section>;
}

export function AgentTrainPage() {
  const search = useSearch({ from: '/train' }), navigate = useNavigate({ from: '/train' });
  const approvals = useContext(ApprovalInboxContext);
  const [agents, setAgents] = useState<{ id: string; title: string }[]>([]), [data, setData] = useState<Data>();
  const [error, setError] = useState<string>(), [attempt, setAttempt] = useState(0);
  const [editor, setEditor] = useState<{ entry: string; hypothesis: string; verdict: HypothesisVerdict; note: string; requestId: string }>();
  const [saving, setSaving] = useState(false), [saveError, setSaveError] = useState<string>();
  const agent = search.agent ?? agents[0]?.id;
  useEffect(() => {
    const controller = new AbortController();
    void request<{ agents: { id: string; title: string }[] }>('/agents', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setAgents(value.agents); })
      .catch(failure => { if (!controller.signal.aborted) setError((failure as Error).message); });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    if (!agent) return;
    let disposed = false, active: AbortController | undefined;
    setData(prior => prior?.workflow.agent.id === agent && (prior.loop?.goalId ?? '') === (search.goal ?? '') ? prior : undefined);
    setEditor(undefined); setError(undefined);
    const refresh = async () => {
      if (active) return;
      const controller = new AbortController(); active = controller;
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const [workflow, versions, runs, loop] = await Promise.all([
          request<AgentWorkflow>(`/agents/${agentName(agent)}/rsi`, { signal: controller.signal }),
          request<{ versions: HarnessVersion[] }>(`/agents/${agentName(agent)}/versions`, { signal: controller.signal }),
          request<{ runs: AgentRun[] }>(`/agent-runs?agent=${encodeURIComponent(agent)}`, { signal: controller.signal }),
          search.goal ? request<AgentLoop>(`/loops/${encodeURIComponent(search.goal)}`, { signal: controller.signal }) : undefined,
        ]);
        if (loop && loop.layer !== 'agent') throw new Error('此目标属于具身层，请切换层级。');
        if (loop?.plans.some(plan => plan.agentId !== agent)) throw new Error('此 Goal 不属于所选 Agent。');
        if (!disposed) { setData({ workflow, versions: versions.versions, runs: runs.runs, loop }); setError(undefined); }
      } catch (failure) { if (!disposed) setError(controller.signal.aborted ? '读取超时，请重试' : (failure as Error).message); }
      finally { clearTimeout(timeout); active = undefined; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 4000);
    return () => { disposed = true; active?.abort(); clearInterval(timer); };
  }, [agent, search.goal, attempt]);
  const goals = [...new Set(data?.workflow.plans.map(plan => plan.goalId) ?? [])];
  const latestGoal = data?.workflow.plans.at(-1)?.goalId;
  useEffect(() => {
    if (agent && (!search.agent || !search.goal && latestGoal)) void navigate({ to: '/train', replace: true,
      search: previous => ({ ...previous, layer: 'agent', agent, goal: previous.goal ?? latestGoal }), resetScroll: false });
  }, [agent, search.agent, search.goal, latestGoal, navigate]);
  const plans = data?.workflow.plans.filter(plan => !search.goal || plan.goalId === search.goal) ?? [];
  const latest = plans.at(-1);
  const references = data?.workflow.baselines.filter(value => latest && value.harnessVersion === data.workflow.agent.referenceVersion
    && value.suite === latest.suite && value.conditions.suiteContentHash === latest.context.reference.conditions.suiteContentHash
    && value.runtime.name === latest.runtime.name && value.runtime.version === latest.runtime.version
    && value.runtime.model === latest.runtime.model && value.runtime.thinking === latest.runtime.thinking) ?? [];
  const reference = references.find(value => value.comparisonState === 'known') ?? references[0];
  const currentReference = reference?.comparisonState === 'known' && reference.score != null ? reference : undefined;
  const pending = approvals.inbox?.approvals.filter(record => (record.status === 'pending'
    || record.status === 'approved' && record.details.agentPromotionId && plans.some(plan => plan.promotions?.some(
      value => value.id === record.details.agentPromotionId && value.status === 'applying'
    ))) && (
    record.details.plan?.layer === 'agent' && record.details.plan.agentId === agent && (!search.goal || record.details.plan.goalId === search.goal)
    || record.details.agentPromotionId && record.details.agentId === agent
  ));
  const saveOverride = async () => {
    if (!search.goal || !editor || !editor.note.trim()) return;
    setSaving(true); setSaveError(undefined);
    try {
      await request(`/notebooks/${encodeURIComponent(search.goal)}/${encodeURIComponent(editor.entry)}`, {
        method: 'PATCH', body: JSON.stringify({ hypothesisId: editor.hypothesis, verdict: editor.verdict, note: editor.note, requestId: editor.requestId }),
      }); setEditor(undefined); setAttempt(value => value + 1);
    } catch (failure) { setSaveError((failure as Error).message); }
    finally { setSaving(false); }
  };
  return <main className="training-page agent-training-page" data-testid="agent-training-page"><header className="training-heading"><div><span className="eyebrow">AGENT RSI</span><h1>Agent 训练</h1><p>固定条件下，观察一个 Harness 变更带来的实际效果。</p></div><div className="training-layer"><Link to="/train" search={{ layer: 'embodied' }}>具身层</Link><span aria-current="page">Agent 层 · {agent ?? '选择 Agent'}</span></div></header>
    <nav className="training-stages" aria-label="Agent 层阶段">{[['harness', 'Harness', '版本与固定组成'], ['scores', '评估集', latest?.suite ?? '选择固定 Suite'], ['runs', '运行', '外部 Target 与平台证据'], ['scores', '评分', '均分与实际范围'], ['notebook', '接受', '训练决定与人类晋升'], ['budget', '成本', '原生用量与 Goal 预算']].map(([id, title, caption], index) => <a key={title} href={`#${id}`}><small>0{index + 1}</small><strong>{title}</strong><span>{caption}</span></a>)}</nav>
    <div className="training-filters"><label>Agent<select aria-label="训练 Agent" value={agent ?? ''} onChange={event => void navigate({ to: '/train', search: { layer: 'agent', agent: event.target.value }, resetScroll: false })}>{agents.map(value => <option key={value.id} value={value.id}>{value.title} · {value.id}</option>)}</select></label><label>Goal<select aria-label="Agent 训练 Goal" value={search.goal ?? ''} onChange={event => void navigate({ to: '/train', search: previous => ({ ...previous, goal: event.target.value || undefined }), resetScroll: false })}><option value="">{goals.length ? '选择 Goal' : '暂无 Goal'}</option>{[...new Set([...goals, ...(search.goal ? [search.goal] : [])])].map(value => <option key={value} value={value}>{value}</option>)}</select></label><button onClick={() => setAttempt(value => value + 1)}>刷新 Agent 记录</button></div>
    {error && <p className="resource-error" role="alert">Agent 记录暂不可用：{error}{data && '。保留最近一次结果。'} <button onClick={() => setAttempt(value => value + 1)}>重试</button></p>}
    {!data ? !error && <p role="status">正在读取 Agent 训练记录…</p> : <>
      {!data.workflow.auditHealthy && <p role="alert" className="resource-error">工具 Trace 暂不可用，新的工作权限已阻止。请保留现有记录并恢复审计。</p>}
      {latest?.activation?.status === 'blocked' && <p role="alert" className="resource-error">候选激活受阻，当前编辑已保留。解决文件冲突后，请重试原执行请求；尚未派发新的评估。</p>}
      {latest?.stop && <p role="status" className="resource-error">污染停止 · {latest.stop.application === 'complete' ? '已恢复 Reference 文件' : `恢复仍受阻：${latest.stop.application}`}。本轮不能继续或接受。</p>}
      {latest?.result?.application === 'blocked' && <p role="alert" className="resource-error">Harness 恢复受阻，请保留编辑并处理文件冲突后重试。</p>}
      <div className="agent-reference-pair"><section data-testid="agent-current-reference"><span className="eyebrow">REFERENCE</span><h2>Harness v{data.workflow.agent.referenceVersion}</h2><strong>{agentMean(reference?.score)}</strong><p>{currentReference ? `测量 ${date(currentReference.at)}` : '当前条件待重新评估'}</p><code>{reference?.harnessHash ?? '等待完整训练证据'}</code></section><section><span className="eyebrow">CANDIDATE</span><h2>{latest ? `Harness v${latest.candidateVersion}` : '暂无候选轮次'}</h2><strong>{agentMean(latest?.result?.score)}</strong><p>{latest ? stateText[latest.status] ?? latest.status : '通过受限 Optimizer 提出一个变更'}</p><code>当前编辑版本 v{data.workflow.agent.currentVersion}</code></section></div>
      <div className="agent-dashboard-grid"><section className="resource-panel" id="scores"><header><h2>版本表现</h2><span className="resource-tag">0–100 · observed range</span></header><div className="resource-panel-body"><AgentScoreChart entries={data.loop?.rounds ?? []} reference={currentReference} /></div></section><section className="resource-panel" id="budget"><header><h2>Goal 预算与成本</h2></header><div className="resource-panel-body">{data.loop ? <><dl className="agent-budget-facts"><div><dt>题次</dt><dd>{data.loop.budget.attemptedCases} 已尝试 / {data.loop.budget.committedCases} 已占用<small>原始上限 {data.loop.budget.limits?.cases ?? '未知'} · 剩余 {data.loop.budget.remainingCases ?? '未知'}</small></dd></div><div><dt>原始时钟</dt><dd>{data.loop.budget.basis ? new Date(data.loop.budget.basis.startedAtMs).toLocaleString() : '等待首次审批'}<small>截止 {date(data.loop.budget.deadlineAt)}{data.loop.budget.expired && ' · 已到期'}</small></dd></div><div><dt>最新原生 LLM 观测</dt><dd>{usd(data.loop.budget.usage.llmUsd)}<small>{data.loop.budget.usage.costSource} · {date(data.loop.budget.usage.observedAt)}</small></dd></div></dl><p>原生报告计入全部实际用量；后续报告可增加实时总额，历史完成快照保留原始观测。</p>{data.loop.budget.approvedExceptions.map(value => <small key={`${value.planId}:${value.revision}`}>有限人工例外 r{value.revision}：{value.extension.reason}</small>)}<p>已观测支出可阻止后续准入；外部运行不提供调用前的硬 USD 保证。</p></> : <p>选择已有 Goal 查看统一预算池。</p>}</div></section></div>
      <section className="resource-panel" id="notebook"><header><h2>实验记录</h2><span className="resource-tag">Notebook · {data.loop?.rounds.length ?? 0} 轮</span></header><div className="resource-panel-body"><div className="resource-table-scroll"><table aria-label="Agent 实验记录"><thead><tr><th>轮次 / 版本</th><th>评分 / 重复</th><th>剩余差距缩小</th><th>假设与人工审计</th><th>决定 / 文件 / 成本</th></tr></thead><tbody>{data.loop?.rounds.map(entry => <tr key={entry.entryId}><td><Link to="/plans/$planId" params={{ planId: entry.plan.id }}>{entry.plan.title}</Link><small>v{entry.candidateVersion} · r{entry.plan.revision}</small></td><td>{agentMean(entry.score?.score)}<small>{entry.score ? `${entry.score.minimum}–${entry.score.maximum} · ${entry.score.cases} 题 × ${entry.score.runs} 次` : entry.errorCode ?? '无完整评分'} · 尝试 {entry.attemptedCases}</small></td><td>{entry.comparison.remainingGapReduction == null ? '未知' : `${(entry.comparison.remainingGapReduction * 100).toFixed(2)}%`}<small>{entry.comparison.gapReason === 'reference_at_ceiling' ? '参照已满分，分母为零' : entry.comparison.gapReason ?? '(候选 − 参照) / (100 − 参照)'}</small></td><td>{entry.hypotheses.map(hypothesis => <div key={hypothesis.definition.id}><strong>{hypothesis.definition.id} · {hypothesis.effectiveVerdict ?? hypothesis.automatic.verdict}</strong><small>{hypothesis.definition.cause}</small><small>自动观测 {observed(hypothesis.automatic.observed)}</small>{hypothesis.overrides.map(audit => <small key={audit.requestId}>{audit.actor.name}：{audit.note}</small>)}{approvals.inbox?.role === 'operator' && <button onClick={() => { setSaveError(undefined); setEditor({ entry: entry.entryId, hypothesis: hypothesis.definition.id, verdict: hypothesis.effectiveVerdict ?? hypothesis.automatic.verdict, note: '', requestId: crypto.randomUUID() }); }}>人工改判 {hypothesis.definition.id}</button>}</div>)}</td><td>{stateText[entry.currentDisposition === 'contaminated' ? 'contaminated' : entry.reportedDecision] ?? entry.reportedDecision}{entry.currentDisposition === 'contaminated' && entry.reportedDecision !== 'contaminated' && <small>历史自动：{stateText[entry.reportedDecision] ?? entry.reportedDecision}</small>}<small>文件 {entry.files.status} · {entry.comparison.state}</small><small>完成快照 {usd(entry.cost.llmUsd)} · {date(entry.cost.observedAt)}</small></td></tr>)}</tbody></table></div>{!data.loop?.rounds.length && <p>暂无已结算轮次；失败、取消与污染停止的尝试也会保留。</p>}
        {editor && <form className="rsi-override" onSubmit={event => { event.preventDefault(); void saveOverride(); }}><h3>人工改判 · {editor.hypothesis}</h3><label>有效判定<select aria-label="Agent 人工假设判定" value={editor.verdict} onChange={event => setEditor({ ...editor, verdict: event.target.value as HypothesisVerdict })}>{(['confirmed', 'refuted', 'inconclusive'] as const).map(value => <option key={value}>{value}</option>)}</select></label><label>改判说明<textarea aria-label="Agent 改判说明" required value={editor.note} onChange={event => setEditor({ ...editor, note: event.target.value })} /></label>{saveError && <p role="alert">{saveError}</p>}<button disabled={saving || !editor.note.trim()}>记录人工判定</button><button type="button" disabled={saving} onClick={() => setEditor(undefined)}>取消改判</button></form>}
      </div></section>
      <section aria-label="Agent 计划与晋升审批"><div className="training-section-heading"><h2>计划与人类晋升</h2><Link to="/approvals">打开审批 →</Link></div>{pending?.map(record => <ApprovalCard key={record.approvalId} record={record} canDecide={approvals.inbox?.role === 'operator' && !approvals.error} />)}{plans.map(plan => <p key={plan.id}><Link to="/plans/$planId" params={{ planId: plan.id }}>{plan.title}</Link> · r{plan.revision} · {stateText[plan.status] ?? plan.status}{plan.promotions?.map(value => <span key={value.id}> · 晋升 {value.status}</span>)}</p>)}{!plans.length && <p>尚无 Agent 计划。完成正式基线后，使用 Harness 优化工作流提交一个有界变更。</p>}</section>
      <div className="agent-dashboard-grid"><section className="resource-panel"><header><h2>Harness 版本</h2></header><div className="resource-panel-body"><table aria-label="Harness 版本历史"><thead><tr><th>版本</th><th>决定</th><th>内容固定</th></tr></thead><tbody>{data.versions.map(version => <tr key={version.version}><th>v{version.version}</th><td>{stateText[version.decision] ?? version.decision}{version.version === data.workflow.agent.referenceVersion && ' · Reference'}{version.version === data.workflow.agent.currentVersion && ' · 当前'}</td><td><code>{version.contentHash.slice(0, 16)}</code></td></tr>)}</tbody></table></div></section><HarnessComposition agent={agent!} initialVersion={data.workflow.agent.referenceVersion} versions={data.versions} /></div>
      <section className="resource-panel" id="runs"><header><h2>Agent 外部运行</h2><span className="resource-tag">公开 train / verification</span></header><div className="resource-panel-body"><div className="resource-table-scroll"><table aria-label="Agent 运行记录"><thead><tr><th>运行 / Harness</th><th>条件 / 状态</th><th>平台评分</th><th>证据 / 清理</th></tr></thead><tbody>{data.runs.filter(run => !search.goal || plans.some(plan => plan.runId === run.id || plan.referenceEvaluationId === run.id)).map(run => <tr key={run.id}><td><code>{run.id}</code><small>Harness v{run.harnessVersion}</small></td><td>{run.runtime.name} · {run.runtime.model}<small>{stateText[run.status] ?? run.status} · {run.cases.filter(value => value.status === 'completed').length}/{run.cases.length} 题次</small></td><td>{agentMean(run.result?.score)}</td><td><Link to="/eval/$suiteId" params={{ suiteId: run.suite }}>查看评估证据 →</Link><small>Source {run.cleanup.complete ? '清理完成' : run.cleanup.blocked ? '清理受阻' : '尚在运行'}</small></td></tr>)}</tbody></table></div>{!data.runs.length && <p>暂无外部运行。</p>}</div></section>
    </>}
  </main>;
}
