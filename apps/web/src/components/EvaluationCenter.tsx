import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../lib/api';
import { useEvaluationCenter } from '../lib/evaluation-hooks';
import { compareEvaluations, currentReference, evaluationDate, scoreGroups, shortVersion, successRate, suiteSegment,
  type EvaluationConditions, type EvaluationRecord, type EvaluationScore, type EvaluationSuite } from '../lib/evaluations';
import { Icon } from './Icon';
import '../evaluations.css';

const decisionLabels = { baseline: '正式基线', accepted: '已接受', rejected: '未接受' };
const statusLabels: Record<string, string> = { queued: '排队中', running: '评估中', completed: '已完成', failed: '失败', cancelled: '已取消' };
const roles = [
  { name: 'Builder', task: '构建评估集', reads: '本地包内的全部题目、评分条件与被测对象。', hidden: '由 Builder 直接编辑本地包；公共 API 不提供私有内容。' },
  { name: 'Optimizer', task: '改进被测对象', reads: '公开题面、train 计分板、train 录制与 Trace、当前版本。', hidden: '不可见私有评分条件、test 题目与结果、种子。' },
  { name: 'Evaluator', task: '平台执行评分', reads: '评分条件、本次 Trace 与状态。', hidden: '不读取其他对象或凭据。' },
  { name: 'Target', task: 'Agent / Controller', reads: '本次公开题面。', hidden: '不可见评分条件。' },
];

function RoleVisibility() {
  return <section className="eval-panel eval-roles" aria-labelledby="eval-roles-title">
    <header><div><span className="eyebrow">INFORMATION BOUNDARY</span><h2 id="eval-roles-title">角色与可见性</h2></div><Icon name="lock" /></header>
    <div className="eval-role-grid">{roles.map(role => <article key={role.name}><h3>{role.name}</h3><span>{role.task}</span>
      <p>{role.reads}</p><small>{role.hidden}</small></article>)}</div>
  </section>;
}

function AcceptanceRules() {
  return <section className="eval-panel eval-rules"><span className="eyebrow">ACCEPTANCE</span><h2>接受规则</h2>
    <p>具身 Candidate 的 Wilson 95% 置信区间下界，必须严格高于同条件 Reference 的成功率点估计。</p>
    <p>首次完整评估成为正式基线。后续已接受版本成为新的参照；未接受的候选仍保留在计分板。</p>
    <div className="eval-rule-formula">CI<sub>95%, lower</sub>(Candidate) &gt; score(Reference)</div>
    <small>held-out test 仅在申请提升时运行并揭示。本页只读取 train；提升流程尚未接入。</small>
  </section>;
}

function SuiteCard({ suite, rows }: { suite: EvaluationSuite; rows: EvaluationScore[] }) {
  const supported = suite.layer === 'embodied';
  const groups = scoreGroups(rows);
  const references = groups.map(currentReference).filter((row): row is EvaluationScore => Boolean(row));
  const candidate = [...rows].reverse().find(row => row.decision === 'accepted' || row.decision === 'rejected');
  return <article className="eval-suite-card" data-suite-id={suite.id}>
    <div className="eval-card-top"><span className="eval-tag accent">{supported ? '具身 · EMBODIED' : 'Agent · 尚未接入'}</span><span className="eval-tag">train</span></div>
    <h2>{suite.title}</h2><code className="eval-suite-id">{suite.id}</code>
    {supported ? <><p>目标：在固定场景中完成 <code>{suite.implements}</code>，以任务成功率评估。</p>
      <dl className="eval-metadata"><div><dt>被测对象</dt><dd>{suite.target}</dd></div><div><dt>环境</dt><dd>{suite.scene} · {suite.source}</dd></div>
        <div><dt>公开划分</dt><dd>{suite.episodes.length} 个 train 回合 · test 锁定</dd></div>
        <div><dt>当前参照</dt><dd>{references.length === 1 ? `${shortVersion(references[0].version)} · ${successRate(references[0].score)}` : references.length ? `${references.length} 组条件各自保留参照` : '尚无正式基线'}</dd></div>
        <div><dt>最近候选</dt><dd>{candidate ? `${shortVersion(candidate.version)} · ${decisionLabels[candidate.decision]}` : '尚无候选记录'}</dd></div></dl>
      <div className="eval-version-trail" aria-label="版本轨迹">{rows.length ? rows.slice(-6).map(row => <span key={row.evaluationId} title={`${row.evaluationId} · ${row.version} · ${decisionLabels[row.decision]}`}
        className={`eval-decision ${row.decision}`}>{shortVersion(row.version)}</span>) : <span>版本轨迹等待首次完整评估</span>}</div></> :
      <p>此层的题目与运行器尚未接入，暂无可展示的评估明细。</p>}
    <Link to="/eval/$suiteId" params={{ suiteId: suiteSegment(suite.id) }} className="eval-open">打开计分板 <Icon name="chevron" /></Link>
  </article>;
}

function RecentEvaluations({ evaluations }: { evaluations: EvaluationRecord[] }) {
  return <section className="eval-panel"><header><div><span className="eyebrow">RECENT RUNS</span><h2>最近评估</h2></div><span>{evaluations.length} 次</span></header>
    {!evaluations.length ? <p className="eval-empty">尚无 train 评估。完成一次固定评估集运行后，结果会出现在这里。</p> :
      <div className="eval-table-scroll"><table aria-label="最近评估"><thead><tr><th>评估 / 评估集</th><th>被测程序</th><th>状态</th><th>成功率</th><th>日期</th></tr></thead><tbody>
        {[...evaluations].reverse().slice(0, 12).map(value => <tr key={value.evaluationId}><td><Link to="/eval/$suiteId" params={{ suiteId: suiteSegment(value.suite) }}>{value.evaluationId}</Link><small>{value.suite}</small></td>
          <td><code>{value.subject}</code></td><td>{statusLabels[value.status] ?? value.status}{value.error && <small>{value.error.code}</small>}</td>
          <td className="numeric">{value.result ? successRate(value.result.score) : '—'}</td><td>{evaluationDate(value.at)}</td></tr>)}</tbody></table></div>}
  </section>;
}

const conditionNames: Record<string, string> = { suite: 'Suite', suiteContentHash: '评估集内容', scene: '场景', sceneContentHash: '场景内容',
  source: '物理源', newtonVersion: 'Newton', solver: '求解器', target: '目标实体', bindingHash: '绑定', embodiment: 'Embodiment', model: '模型', thinking: '思考等级' };
function Conditions({ suite, row }: { suite: EvaluationSuite; row?: EvaluationScore }) {
  const conditions: EvaluationConditions = row?.key ?? { suite: suite.id, suiteContentHash: suite.contentHash, scene: suite.scene,
    sceneContentHash: suite.sceneContentHash, source: suite.source, target: suite.target };
  return <section className="eval-conditions" aria-label="可比较条件"><div><span className="eyebrow">COMPARABLE CONDITIONS</span><h2>可比较条件</h2></div>
    <div className="eval-condition-tags">{Object.entries(conditions).map(([name, value]) => <span key={name} className={`eval-tag ${['model', 'thinking'].includes(name) ? 'accent' : ''}`} title={`${conditionNames[name] ?? name}: ${value ?? '未声明'}`}>
      {conditionNames[name] ?? name} <b>{value == null ? name === 'newtonVersion' && suite.source === 'synthetic' ? '不适用（Synthetic）' : '未声明' : name.endsWith('Hash') ? shortVersion(value) : value}</b></span>)}</div>
    <p>{row ? `当前条件组 ${row.conditionsGroup ?? row.evaluationId}` : '尚无运行条件；以上为评估集固定配置。'} · 只有完整条件相同的记录才可比较，并绘制在同一条曲线上。Embodiment 未注册时以实际目标与绑定记录为准。</p>
  </section>;
}

function ScoreCurve({ rows }: { rows: EvaluationScore[] }) {
  const groups = scoreGroups(rows);
  return <section className="eval-panel"><header><div><span className="eyebrow">VERSION HISTORY</span><h2>分数曲线</h2></div><span>成功率 · train</span></header>
    {!groups.length ? <p className="eval-empty">尚无完整评估，暂无分数曲线。</p> : <div className="eval-curves">{groups.map((group, index) => {
      const x = (i: number) => group.length === 1 ? 280 : 44 + i * 492 / (group.length - 1);
      const y = (score: number) => 164 - score * 140;
      return <figure key={group[0].evaluationId}><figcaption><b>条件 {index + 1}</b> · {group[0].subject}<small>{group[0].key.scene} · {group[0].key.source} · {group.length} 次评估</small></figcaption>
        <svg viewBox="0 0 560 200" role="img" aria-label={`条件 ${index + 1} 的成功率曲线，${group.length} 次评估`}>
          {[0, .5, 1].map(value => <g key={value}><line x1="40" x2="540" y1={y(value)} y2={y(value)} className="eval-chart-grid" /><text x="2" y={y(value) + 4}>{value * 100}%</text></g>)}
          <path d={group.map((row, i) => `${i ? 'L' : 'M'}${x(i)},${y(row.score)}`).join(' ')} className="eval-chart-line" />
          {group.map((row, i) => <circle key={row.evaluationId} cx={x(i)} cy={y(row.score)} r="4" className={`eval-chart-point ${row.decision}`}>
            <title>{`${row.evaluationId} · ${shortVersion(row.version)} · ${successRate(row.score)} · ${decisionLabels[row.decision]}`}</title></circle>)}
          <text x="40" y="190">第 1 次</text><text x="540" y="190" textAnchor="end">{group.length > 1 ? `第 ${group.length} 次` : '单次观测'}</text>
        </svg><details><summary>查看曲线数值</summary><ol>{group.map(row => <li key={row.evaluationId}><code>{row.evaluationId}</code> · {successRate(row.score)}</li>)}</ol></details>
      </figure>;
    })}</div>}
    <p className="eval-footnote">横轴按本组评估的追加顺序。每个条件组单独绘制，版本与 95% CI 见计分板。</p>
  </section>;
}

function Comparison({ suite, rows }: { suite: string; rows: EvaluationScore[] }) {
  const [candidate, setCandidate] = useState(''), [reference, setReference] = useState('');
  const [result, setResult] = useState<{ identity: string; delta?: number; error?: string; pending?: boolean }>();
  const active = useRef<AbortController | undefined>(undefined);
  const identity = `${candidate}:${reference}`;
  useEffect(() => () => active.current?.abort(), [identity]);
  const compare = async () => {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    const timeout = setTimeout(() => controller.abort(), 8000);
    setResult({ identity, pending: true });
    try {
      const value = await compareEvaluations(suite, candidate, reference, controller.signal);
      if (!controller.signal.aborted) setResult({ identity, delta: value.comparison.scoreDelta });
    } catch (error) {
      if (active.current === controller) setResult({ identity, error: error instanceof ApiError &&
        (error.detail as { code?: string })?.code === 'not_comparable' ? 'not_comparable · 完整条件不同，不能比较。' :
        controller.signal.aborted ? '比较超时，请重试。' : (error as Error).message });
    } finally { clearTimeout(timeout); }
  };
  const current = result?.identity === identity ? result : undefined;
  return <div className="eval-comparison" aria-label="比较评估"><label>候选<select aria-label="比较候选" value={candidate} onChange={event => setCandidate(event.target.value)}><option value="">选择评估</option>
    {rows.map(row => <option key={row.evaluationId} value={row.evaluationId}>{row.evaluationId} · {successRate(row.score)}</option>)}</select></label>
    <label>对照<select aria-label="比较对照" value={reference} onChange={event => setReference(event.target.value)}><option value="">选择评估</option>
      {rows.map(row => <option key={row.evaluationId} value={row.evaluationId}>{row.evaluationId} · {successRate(row.score)}</option>)}</select></label>
    <button onClick={() => void compare()} disabled={!candidate || !reference || current?.pending}>比较</button>
    <span role="status">{current?.pending ? '正在比较…' : current?.error ?? (current?.delta !== undefined ? `成功率变化 ${current.delta >= 0 ? '+' : ''}${(current.delta * 100).toFixed(1)} 个百分点` : '由平台核对完整条件')}</span>
  </div>;
}

function Scoreboard({ suite, rows, selected, select }: { suite: EvaluationSuite; rows: EvaluationScore[]; selected?: string; select: (id: string) => void }) {
  const groups = scoreGroups(rows);
  const referenceIds = new Set(groups.map(group => currentReference(group)?.evaluationId));
  return <section className="eval-panel"><header><div><span className="eyebrow">APPEND-ONLY · TRAIN</span><h2>计分板</h2></div><span>{rows.length} 条公开记录</span></header>
    <div className="eval-table-scroll"><table className="eval-scoreboard" aria-label="具身评估计分板"><thead><tr><th>运行代码版本 / 评估</th><th>被测程序 / 控制器</th><th>成功率</th><th>95% CI</th><th>训练成本</th><th>评估时长</th><th>日期</th><th>判定</th></tr></thead><tbody>
      {rows.map(row => <tr key={row.evaluationId} className={`${referenceIds.has(row.evaluationId) ? 'is-reference' : ''} ${selected === row.evaluationId ? 'is-selected' : ''}`} data-evaluation-id={row.evaluationId}>
        <td><button className="eval-row-select" onClick={() => select(row.evaluationId)} aria-pressed={selected === row.evaluationId} aria-label={`查看回合 ${row.evaluationId}`}><code title={String(row.version)}>{shortVersion(row.version)}</code><small>{row.evaluationId}</small></button>
          <span className="eval-tag">条件 {groups.findIndex(group => group.some(value => value.evaluationId === row.evaluationId)) + 1}</span></td>
        <td><code>{row.subject}</code><small>Controller 注册版本：未知</small></td><td className="numeric">{successRate(row.score)}<small>{row.successes} / {row.episodes}</small></td>
        <td className="numeric">{row.ci95 ? `${successRate(row.ci95[0])} – ${successRate(row.ci95[1])}` : '未知'}</td><td className="numeric">{row.cost?.gpuHours == null ? '未知' : `${row.cost.gpuHours} GPU·h`}</td>
        <td className="numeric">{row.durationSeconds.toFixed(1)}s</td><td>{evaluationDate(row.at)}</td><td><span className={`eval-decision ${row.decision}`}>{decisionLabels[row.decision]}</span>
          {referenceIds.has(row.evaluationId) && <small className="eval-reference-label">当前参照</small>}{row.reference && <small title={row.reference}>对照 {row.reference}</small>}</td></tr>)}
      {!rows.length && <tr><td colSpan={8} className="eval-empty">尚无完整 train 评估。首次完整运行将建立正式基线。</td></tr>}
      <tr className="eval-locked" data-testid="held-out-test-row"><th scope="row"><Icon name="lock" /> held-out test</th><td colSpan={7}>锁定 · 仅在申请提升时运行并揭示；题目、种子与结果不公开。</td></tr>
    </tbody></table></div>
    <p className="eval-footnote">版本来自本次评估的实际运行代码指纹。训练成本与已注册 Controller 版本尚未接入。</p>
    {rows.length > 1 && <Comparison key={rows[0].subject} suite={suite.id} rows={rows} />}
  </section>;
}

function EpisodeDetails({ suite, row, record }: { suite: EvaluationSuite; row?: EvaluationScore; record?: EvaluationRecord }) {
  return <section className="eval-panel"><header><div><span className="eyebrow">EMBODIED BREAKDOWN</span><h2>分阶段通过率与评估回合</h2></div><span>{row ? shortVersion(row.version) : '等待评估'}</span></header>
    {row ? <><code className="eval-selected-id">{row.evaluationId}</code><div className="eval-phases">{suite.milestones.map(stage => {
      const phase = row.phases[stage];
      return <div key={stage}><label htmlFor={`phase-${stage}`}>{stage}</label><progress id={`phase-${stage}`} max={1} value={phase?.rate ?? 0} />
        <span>{phase ? `${phase.passed}/${phase.episodes} · ${successRate(phase.rate)}` : '未记录'}</span></div>;
    })}</div>
      {record?.episodes.length ? <div className="eval-table-scroll"><table aria-label="评估回合"><thead><tr><th>train 回合</th><th>任务成功</th>{suite.milestones.map(stage => <th key={stage}>{stage}</th>)}<th>失败阶段</th><th>动作结果</th><th>录制</th></tr></thead><tbody>
        {record.episodes.map((episode, i) => <tr key={`${episode.caseId}:${i}`}><th scope="row">{episode.caseId}</th><td>{episode.success ? '成功' : '失败'}</td>
          {suite.milestones.map(stage => <td key={stage}>{episode.milestones[stage] === undefined ? '未记录' : episode.milestones[stage] ? '通过' : '未通过'}</td>)}
          <td>{episode.failedStage ?? '—'}</td><td>{episode.invocationStatus}<small>{episode.invocationId}</small></td>
          <td>{episode.recordingId ? <Link to="/replay/$recordingId" params={{ recordingId: episode.recordingId }}>查看回放 ↗</Link> : '未记录'}</td></tr>)}</tbody></table></div> : <p className="eval-empty">此计分记录暂无公开回合明细。</p>}
    </> : <p className="eval-empty">完成评估后显示阶段观测与逐回合结果。</p>}
    <details className="eval-public-cases"><summary>公开 train 回合与覆盖</summary><ul>{suite.episodes.map(episode => <li key={episode.id}><code>{episode.id}</code> · {Object.entries(episode.params).map(([name, value]) => `${name}: ${JSON.stringify(value)}`).join(' → ')}</li>)}</ul>
      <dl>{Object.entries(suite.coverage).map(([name, values]) => <div key={name}><dt>{name}</dt><dd>{values.map(value => JSON.stringify(value)).join(', ')}</dd></div>)}</dl></details>
  </section>;
}

function SuiteDetails({ suite, rows, evaluations }: { suite: EvaluationSuite; rows: EvaluationScore[]; evaluations: EvaluationRecord[] }) {
  const [subject, setSubject] = useState(''), [selected, select] = useState('');
  if (suite.layer !== 'embodied') return <><p className="eval-empty">Agent 层题目与运行器尚未接入，暂无可展示的计分板。</p><RoleVisibility /></>;
  const subjects = [...new Set(rows.map(row => row.subject))];
  const filtered = rows.filter(row => !subject || row.subject === subject);
  const row = filtered.find(value => value.evaluationId === selected) ?? filtered.at(-1);
  const record = evaluations.find(value => value.evaluationId === row?.evaluationId);
  return <><Conditions suite={suite} row={row} /><div className="eval-detail-toolbar"><label>被测程序<select aria-label="被测程序筛选" value={subject} onChange={event => { setSubject(event.target.value); select(''); }}>
    <option value="">全部程序</option>{subjects.map(value => <option key={value}>{value}</option>)}</select></label><span>目标 <code>{suite.target}</code> · {suite.implements}</span></div>
    <Scoreboard suite={suite} rows={filtered} selected={row?.evaluationId} select={select} /><ScoreCurve rows={filtered} />
    <EpisodeDetails suite={suite} row={row} record={record} /><div className="eval-bottom-grid"><section className="eval-panel eval-scoring"><span className="eyebrow">SCORING</span><h2>评分方式</h2>
      <p>分数为固定评估集的任务成功率。任务成功依据本次状态判定，与动作调用是否成功分别记录。</p>
      <p>阶段通过率来自运行中的实际观测，阶段通过与最终任务成功分别展示。</p><p><Icon name="lock" /> 私有成功条件、阶段判定条件与初始种子由 Evaluator 读取。</p></section><AcceptanceRules /></div><RoleVisibility />
  </>;
}

export function EvaluationCenter({ suiteId }: { suiteId?: string }) {
  const query = useEvaluationCenter(suiteId);
  const [layer, setLayer] = useState('all');
  const suite = query.data?.suites[0];
  const suites = query.data?.suites.filter(value => layer === 'all' || value.layer === layer);
  return <main className="evaluation-center">
    {suiteId && <Link to="/eval" className="eval-back">← 评估中心</Link>}
    <header className="eval-page-heading"><div><span className="eyebrow">EVALUATION CENTER</span><h1>{suiteId ? suite?.title ?? '评估集' : '评估'}</h1>
      <p>{suiteId ? suiteId : '固定评估集、可比较条件与可追溯的改进。'}</p></div><span className="eval-tag"><Icon name="lock" /> 公开 train 视图</span></header>
    {query.error && <div className="eval-error" role="alert">读取评估失败：{query.error} <button onClick={query.retry}>重试</button>{query.data && <small>下方保留上次成功读取的结果。</small>}</div>}
    {!query.data ? !query.error && <p className="eval-empty" role="status" aria-busy="true">正在读取评估集与计分板…</p> : suiteId ? suite && <SuiteDetails key={suite.id} suite={suite} rows={query.data.rows} evaluations={query.data.evaluations} /> : <>
      <section className="eval-list-toolbar" aria-label="评估集筛选"><div role="group" aria-label="评估层">{[['all', '全部'], ['embodied', '具身'], ['agent', 'Agent']].map(([value, label]) =>
        <button key={value} aria-pressed={layer === value} onClick={() => setLayer(value)}>{label}</button>)}</div><span>{suites?.length ?? 0} 个评估集</span></section>
      {suites?.length ? <div className="eval-suite-grid">{suites.map(value => <SuiteCard key={value.id} suite={value} rows={query.data!.rows.filter(row => row.suite === value.id)} />)}</div> :
        <p className="eval-empty">{layer === 'agent' ? 'Agent 层评估集与运行器尚未接入。' : '暂无此层评估集。'}</p>}
      <RoleVisibility /><div className="eval-bottom-grid"><RecentEvaluations evaluations={query.data.evaluations} /><AcceptanceRules /></div>
    </>}
  </main>;
}
