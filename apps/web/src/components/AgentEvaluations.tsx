import { Link } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { agentCaseStatus, agentRunStatus, cancelAgentRun, createAgentRun, listAgentRuns, listHarnessVersions,
  type AgentRun, type AgentRunInput, type HarnessVersion } from '../lib/agent-runs';
import { agentScore, evaluationDate, shortVersion, type AgentEvaluationSuite, type EvaluationScore } from '../lib/evaluations';
import { runtimeCostSource, runtimeCount, runtimeMoney } from '../lib/runtime-records';
import { Icon } from './Icon';

function CreateRun({ suite, created }: { suite: AgentEvaluationSuite; created: (run: AgentRun) => void }) {
  const agent = suite.target;
  const [versions, setVersions] = useState<HarnessVersion[]>(), [version, setVersion] = useState('');
  const [runtime, setRuntime] = useState(''), [runtimeVersion, setRuntimeVersion] = useState('');
  const [model, setModel] = useState(''), [thinking, setThinking] = useState('');
  const [seconds, setSeconds] = useState('900'), [pending, setPending] = useState(false), [error, setError] = useState<string>();
  const [loadError, setLoadError] = useState<string>(), [attempt, setAttempt] = useState(0);
  const request = useRef<{ content: string; id: string } | undefined>(undefined);
  const mutation = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => mutation.current?.abort(), []);
  useEffect(() => {
    let disposed = false;
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 8000);
    setVersions(undefined); setVersion(''); setLoadError(undefined);
    void listHarnessVersions(agent, controller.signal).then(values => {
      if (controller.signal.aborted) return;
      setVersions(values); setVersion(values.length ? String(values.at(-1)!.version) : '');
    }).catch(error => {
      if (!disposed) setLoadError(controller.signal.aborted ? '读取 Harness 超时，请重试。' : (error as Error).message);
    }).finally(() => clearTimeout(timeout));
    return () => { disposed = true; controller.abort(); };
  }, [agent, attempt]);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); if (pending) return;
    const fields = { suite: suite.id, harnessVersion: Number(version), purpose: 'train' as const,
      runtime: { name: runtime.trim(), version: runtimeVersion.trim() || null, model: model.trim(), thinking: thinking.trim() },
      wallSeconds: Number(seconds) };
    const content = JSON.stringify({ agent, ...fields });
    if (request.current?.content !== content) request.current = { content, id: crypto.randomUUID() };
    const controller = new AbortController(); mutation.current = controller;
    const timeout = setTimeout(() => controller.abort(), 15_000);
    setPending(true); setError(undefined);
    try {
      const run = await createAgentRun(agent, { ...fields, requestId: request.current.id } satisfies AgentRunInput, controller.signal);
      if (!controller.signal.aborted) { created(run); request.current = undefined; }
    } catch (error) {
      setError(controller.signal.aborted ? '创建结果尚未确认，请用相同输入重试。' : (error as Error).message);
    } finally { clearTimeout(timeout); setPending(false); }
  };
  const selected = versions?.find(item => String(item.version) === version);
  return <section className="eval-panel"><header><div><span className="eyebrow">EXTERNAL RUNTIME</span><h2>创建 Agent 评估</h2></div>
    <span>{suite.cases.length} 题 × {suite.runsPerCase} 次 · public train</span></header>
    <form className="eval-agent-form" onSubmit={event => void submit(event)}>
      <p>平台固定题目、Harness 与实验 Session，并记录受信证据和评分。外部 runtime 使用自身的模型、上下文与认证执行。</p>
      {loadError && <p role="alert">读取 Harness 失败：{loadError} <button type="button" onClick={() => setAttempt(value => value + 1)}>重读版本</button></p>}
      <fieldset disabled={pending}><legend>固定评估输入</legend><div className="eval-agent-fields">
        <label>Agent<input aria-label="评估 Agent" value={agent} readOnly /></label>
        <label>Harness 版本<select aria-label="评估 Harness 版本" required value={version} onChange={event => setVersion(event.target.value)}>
          <option value="">{versions ? '选择固定版本' : '正在读取版本…'}</option>
          {versions?.map(item => <option key={item.version} value={item.version}>v{item.version} · {shortVersion(item.contentHash)}</option>)}</select></label>
        <label>Runtime 名称<input required maxLength={120} value={runtime} placeholder="如 claude-code" onChange={event => setRuntime(event.target.value)} /></label>
        <label>Runtime 版本<input maxLength={120} value={runtimeVersion} placeholder="未提供可留空" onChange={event => setRuntimeVersion(event.target.value)} /></label>
        <label>模型声明<input required maxLength={200} value={model} placeholder="外部 runtime 的模型标识" onChange={event => setModel(event.target.value)} /></label>
        <label>思考等级声明<input required maxLength={80} value={thinking} placeholder="如 high / disabled" onChange={event => setThinking(event.target.value)} /></label>
        <label>平台期限（秒）<input required type="number" min="1" max="7200" step="1" value={seconds} onChange={event => setSeconds(event.target.value)} /></label>
      </div></fieldset>
      {selected && <small>Harness 内容 <code>{selected.contentHash}</code> · Archive <code>{selected.archiveHash}</code></small>}
      <small>Suite <code>{suite.id}</code> · {suite.scene} · {suite.source}。模型与思考等级是运行时声明；所有输入在创建时固定。</small>
      {error && <p role="alert">创建失败：{error}</p>}
      <button type="submit" disabled={pending || !selected || Boolean(loadError)}>{pending ? '正在创建…' : '创建完整 train 评估'}</button>
    </form>
  </section>;
}

function RunDetails({ run, cancelled }: { run: AgentRun; cancelled: (run: AgentRun) => void }) {
  const [pending, setPending] = useState(false), [error, setError] = useState<string>();
  const mutation = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => mutation.current?.abort(), []);
  const cancel = async () => {
    const controller = new AbortController(); mutation.current = controller;
    const timeout = setTimeout(() => controller.abort(), 15_000); setPending(true); setError(undefined);
    try { cancelled(await cancelAgentRun(run.id, controller.signal)); }
    catch (error) { setError(controller.signal.aborted ? '取消结果尚未确认，请刷新状态。' : (error as Error).message); }
    finally { clearTimeout(timeout); setPending(false); }
  };
  const finished = ['completed', 'failed', 'cancelled'].includes(run.status);
  return <section className="eval-panel" data-testid="agent-run-detail"><header><div><span className="eyebrow">TRUSTED SESSION EVIDENCE</span><h2>逐题证据与交接</h2></div>
    <span>{agentRunStatus[run.status]}</span></header><div className="eval-agent-run-summary">
    <code>{run.id}</code><dl className="eval-metadata">
      <div><dt>冻结对象</dt><dd>{run.agentId} · Harness v{run.harnessVersion} · {run.purpose === 'train' ? '完整 train' : '单题验证，不发布计分板'}</dd></div>
      <div><dt>运行时声明</dt><dd>{run.runtime.name} · {run.runtime.version ?? '版本未提供'} · {run.runtime.model} · {run.runtime.thinking ?? '思考等级未声明'}</dd></div>
      <div><dt>实验环境</dt><dd>{run.pins.scene} · {run.pins.source}<small>Suite {run.pins.suiteContentHash} · Scene {run.pins.sceneContentHash}</small></dd></div>
      <div><dt>Harness 固定</dt><dd>{run.pins.harnessHash}<small>Archive {run.pins.archiveHash}</small></dd></div>
      <div><dt>Skills 固定</dt><dd>{run.pins.skills.map(item => <small key={item.name}>{item.name} · {item.sha256}</small>)}</dd></div>
      <div><dt>平台期限</dt><dd>{evaluationDate(run.deadlineAt)}</dd></div>
      <div><dt>Source 清理</dt><dd>{run.cleanup.blocked ? '未确认，单活跃 Session 槽仍被占用' : run.cleanup.complete ? '已确认完成' : '尚未完成'}
        <small>取消与到期撤销平台权限，并向外部控制器报告状态。</small></dd></div>
    </dl>
    {run.error && <p role="status">运行原因：<code>{run.error.code}</code></p>}
    {!finished && <><p>由外部控制器领取下一题，为每次重复创建新的 Target 对话。Target 使用当题的专用连接。</p>
      <code className="eval-agent-command">lw agent-run next {run.id} --json</code>
      <button onClick={() => void cancel()} disabled={pending}>{pending ? '正在取消…' : '取消评估'}</button></>}
    {finished && !run.result && <p>此运行没有完整有效的 train 评分，已保留尝试与证据。</p>}
    {error && <p role="alert">{error}</p>}
    </div><div className="eval-table-scroll"><table aria-label="Agent 逐题结果"><thead><tr><th>题目 / 重复</th><th>状态</th><th>平台评分</th><th>Session / 原生记录</th><th>Trace / 录制</th><th>运行时用量</th></tr></thead><tbody>
      {run.cases.map(item => <tr key={item.caseRunId}><th scope="row">{item.caseId} · 第 {item.repeat} 次<small>{item.caseRunId}</small></th>
        <td>{agentCaseStatus[item.status]}{item.error && <small>{item.error.code}</small>}</td><td className="numeric">{agentScore(item.score)}
          {item.dimensions && <details><summary>评分维度</summary>{Object.entries(item.dimensions).map(([name, value]) => <small key={name}>{name}: {value}</small>)}</details>}</td>
        <td><code>{item.sessionId ?? '等待 Session'}</code><small>{item.nativeSessionId ?? '原生会话未报告'}</small>
          {item.runtimeRecordId && <><Link to="/runtime-sessions/$id" params={{ id: item.runtimeRecordId }}>查看原生运行记录 ↗</Link><small>固定内容 {item.runtimeRecordHash}</small></>}
          {item.observedRuntime && <small>原生观测：{item.observedRuntime.name} · {item.observedRuntime.version ?? '版本未知'} · {item.observedRuntime.models.join(', ') || '模型未知'} · {item.observedRuntime.thinking ?? '思考等级未知'}</small>}</td>
        <td>{item.recordingId && <Link to="/replay/$recordingId" params={{ recordingId: item.recordingId }}>查看回放 ↗</Link>}
          {item.artifacts.map(artifact => <div key={artifact.id}><a href={artifact.url} target="_blank" rel="noreferrer">{artifact.kind} ↗</a>
            <small title={artifact.sha256}>{shortVersion(artifact.sha256)} · {artifact.size} B</small></div>)}
          {!item.recordingId && !item.artifacts.length && '尚无证据'}</td>
        <td>{item.usage ? <>{runtimeMoney(item.usage.llmUsd)}<small>{runtimeCostSource[item.usage.costSource]}</small>
          <small>输入 {runtimeCount(item.usage.inputTokens)} · 输出 {runtimeCount(item.usage.outputTokens)}</small>
          <small>缓存读 {runtimeCount(item.usage.cacheReadTokens)} · 写 {runtimeCount(item.usage.cacheWriteTokens)}</small>
          <small>Token 总数 {runtimeCount(item.usage.tokens)} · 请求 {runtimeCount(item.usage.requests)}</small>
          <small>runtime-import · 完成时快照</small></> : '未关联运行时记录，用量未知'}</td></tr>)}
      {!run.cases.length && <tr><td colSpan={6}>等待共享 Session 槽与首次题目交接。</td></tr>}
    </tbody></table></div>
  </section>;
}

export function AgentEvaluations({ suite, rows }: { suite: AgentEvaluationSuite; rows: EvaluationScore[] }) {
  const [runs, setRuns] = useState<AgentRun[]>(), [selected, select] = useState('');
  const [error, setError] = useState<string>(), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let disposed = false, active: AbortController | undefined;
    const read = async () => {
      if (active) return;
      const controller = new AbortController(); active = controller;
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const values = await listAgentRuns(controller.signal);
        if (!disposed) { setRuns(values.filter(run => run.suite === suite.id)); setError(undefined); }
      } catch (error) { if (!disposed) setError(controller.signal.aborted ? '读取运行超时，请重试。' : (error as Error).message); }
      finally { clearTimeout(timeout); active = undefined; }
    };
    void read(); const interval = setInterval(() => void read(), 3000);
    return () => { disposed = true; clearInterval(interval); active?.abort(); };
  }, [suite.id, attempt]);
  const update = (run: AgentRun) => {
    setRuns(values => [run, ...(values ?? []).filter(value => value.id !== run.id)]); select(run.id);
  };
  const run = runs?.find(value => value.id === selected) ?? runs?.at(0);
  return <><CreateRun suite={suite} created={update} />
    <section className="eval-panel"><header><div><span className="eyebrow">APPEND-ONLY · TRAIN</span><h2>Agent 计分板</h2></div><span>{rows.length} 条已报告结果 · 满分 100</span></header>
      <div className="eval-table-scroll"><table aria-label="Agent 评估计分板"><thead><tr><th>Harness / 评估</th><th>模型 / 思考等级</th><th>均分</th><th>完整范围</th><th>运行时成本</th><th>日期</th><th>判定</th></tr></thead><tbody>
        {rows.map(row => <tr key={row.evaluationId}><td><button className="eval-row-select" onClick={() => select(row.evaluationId)}>v{row.version}<small>{row.evaluationId}</small></button><small>{row.subject}</small></td>
          <td>{row.key.model ?? '未知'}<small>{row.key.thinking ?? '未知'} · 声明条件</small><small>条件组 {row.conditionsGroup ?? row.evaluationId}</small></td>
          <td className="numeric">{agentScore(row.score)}<small>CI 不适用</small></td><td>{row.episodes} 次实验<small>每题 {suite.runsPerCase} 次</small></td>
          <td className="numeric">{runtimeMoney(row.cost?.llmUsd)}<small>{row.cost?.costSource === 'runtime-mixed' ? '运行时报告 / 估算混合' : runtimeCostSource[row.cost?.costSource ?? 'unknown']}</small>
            <small>Token {runtimeCount(row.cost?.tokens)} · 请求 {runtimeCount(row.cost?.requests)}</small>
            <small>{row.cost?.observationBasis === 'completion-snapshot' ? '完成时快照 · 原生记录去重' : '观测口径未提供'}</small></td>
          <td>{evaluationDate(row.at)}</td><td>已报告<small>接受 / Reference / 提升独立</small></td></tr>)}
        {!rows.length && <tr><td colSpan={7} className="eval-empty">尚无完整有效的 Agent train 运行记录。</td></tr>}
        <tr className="eval-locked" data-testid="held-out-test-row"><th scope="row"><Icon name="lock" /> held-out test</th><td colSpan={6}>锁定 · 仅在申请提升时运行并揭示；题目与结果不公开。</td></tr>
      </tbody></table></div>
      <p className="eval-footnote">平台依据可信 Session 证据评分。导入的运行时记录提供消息与用量；完成信号不携带评分。</p>
    </section>
    <section className="eval-panel"><header><div><span className="eyebrow">CONTROLLER HANDOFF</span><h2>外部评估运行</h2></div><button onClick={() => setAttempt(value => value + 1)}>刷新运行</button></header>
      {error && <p className="eval-error" role="alert">刷新失败：{error} {runs && '保留上次读取的运行。'}</p>}
      {!runs ? <p className="eval-empty" role="status">正在读取评估运行…</p> : !runs.length ? <p className="eval-empty">创建评估后，由外部 runtime 控制器领取公开题目。</p> :
        <div className="eval-agent-run-list" role="group" aria-label="选择 Agent 评估运行">{runs.map(item => <button key={item.id} aria-pressed={run?.id === item.id} onClick={() => select(item.id)}>
          <code>{item.id}</code><span>{agentRunStatus[item.status]} · v{item.harnessVersion}</span></button>)}</div>}
    </section>
    {run && <RunDetails key={run.id} run={run} cancelled={update} />}
  </>;
}
