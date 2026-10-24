import type { TrainingPlanContent, TrainingPlanChange, AgentFrozenInputs } from '../lib/training';
import '../training-plans.css';

const value = (item: unknown) => item == null ? '—' : typeof item === 'string' ? item : JSON.stringify(item, null, 2);

function Interview({ transcript }: { transcript: string }) {
  const lines = transcript.trim().split(/\r?\n/);
  const rows = lines.map(line => line.trim().startsWith('|') && line.trim().endsWith('|')
    ? line.trim().slice(1, -1).split('|').map(cell => cell.trim()) : []);
  const table = rows.length > 2 && rows.every(row => row.length === 3)
    && rows[1].every(cell => /^:?-{3,}:?$/.test(cell));
  if (!table) return <pre className="training-interview">{transcript}</pre>;
  return <><div className="training-diff-scroll"><table aria-label="训练访谈问答"><thead><tr>
    {rows[0].map((label, index) => <th key={index}>{label}</th>)}</tr></thead><tbody>
    {rows.slice(2).map((row, index) => <tr key={index}>{row.map((text, column) => <td key={column}>{text}</td>)}</tr>)}
  </tbody></table></div><details><summary>完整访谈原文</summary><pre className="training-interview">{transcript}</pre></details></>;
}

export function TrainingPlanDetails({ plan, changes = [], frozenInputs }: {
  plan: TrainingPlanContent; changes?: TrainingPlanChange[]; frozenInputs?: AgentFrozenInputs;
}) {
  return <div className="training-plan-details" data-testid="training-plan-details">
    <section aria-label="训练计划内容"><h3>{plan.layer === 'agent' ? 'Agent 运行计划' : '训练计划'}</h3>
      <dl className="training-plan-facts">
        <div><dt>目标</dt><dd>{plan.goal}</dd></div>
        {plan.goalId && <div><dt>Goal</dt><dd><code>{plan.goalId}</code></dd></div>}
        {plan.layer === 'agent' ? <>
          <div><dt>Agent / Harness</dt><dd><code>{plan.agent.id}</code> · v{plan.agent.harnessVersion}</dd></div>
          <div><dt>固定评估集</dt><dd><code>{plan.suite}</code> · train</dd></div>
          <div><dt>模型 / 思考等级</dt><dd>{plan.model.ref} · {plan.model.thinking}</dd></div>
          <div><dt>凭据引用</dt><dd><code>{plan.credentialRef}</code></dd></div>
          <div><dt>运行范围</dt><dd>{frozenInputs ? `${frozenInputs.suite.cases.length} 题 × ${frozenInputs.suite.runsPerCase} 次 = ${frozenInputs.caseRunsRequired} 题次` : '固定范围证据未知'}</dd></div>
          <div><dt>执行配置</dt><dd>受限运行 · {plan.executionProfile}</dd></div>
          <div><dt>本计划共享额度</dt><dd>{plan.budget.caseRuns} 题次 · {plan.budget.requests} 请求 · {plan.budget.tokens.toLocaleString()} Token · ${plan.budget.llmUsd} · {plan.budget.wallSeconds}s 执行时间</dd></div>
          <div><dt>单次输出上限</dt><dd>{plan.budget.maxOutputTokensPerRequest.toLocaleString()} Token</dd></div>
          <div><dt>本计划声明的目标预算</dt><dd>{plan.goalBudget.caseRuns} 题次 · ${plan.goalBudget.llmUsd} · {plan.goalBudget.days} 天；已有目标以上次人审来源为准。</dd></div>
          {frozenInputs && <><div><dt>固定输入摘要</dt><dd><code>{frozenInputs.immutablePlanInputHash}</code></dd></div>
            <div><dt>Harness 归档</dt><dd><code>{frozenInputs.harness.archiveHash}</code></dd></div>
            <div><dt>锁定技能</dt><dd>{frozenInputs.harness.skills.map(skill => <p key={skill.name}>{skill.name} · <code>{skill.sha256}</code></p>)}</dd></div>
            <div><dt>执行约束覆盖</dt><dd>{Object.entries(frozenInputs.adapter.enforcement).map(([name, coverage]) => `${name}: ${coverage}`).join(' · ')} · 上下文界限 {frozenInputs.adapter.contextTokens} Token · 价格 r{frozenInputs.priceQuote.revision}</dd></div></>}
        </> : <><div><dt>Task 版本</dt><dd><code>{plan.task}</code></dd></div>
        <div><dt>算法</dt><dd><pre>{value(plan.algorithm)}</pre></dd></div>
        <div><dt>模型</dt><dd><pre>{value(plan.model)}</pre></dd></div>
        <div><dt>规模与超参</dt><dd><pre>{value(plan.hyperparameters)}</pre></dd></div>
        <div><dt>作业种子</dt><dd>{plan.jobs.seeds.join(', ') || '无训练作业'}</dd></div>
        <div><dt>GPU 预算</dt><dd>{plan.budget.gpuHours} 小时</dd></div>
        <div><dt>成功标准</dt><dd>{plan.evaluation.scene} · {plan.evaluation.episodes} 回合
          {plan.evaluation.threshold != null && ` · 至少 ${plan.evaluation.threshold} 次成功`}</dd></div></>}
      </dl>
      <h4>可测量假设</h4><ol className="training-hypotheses">{plan.hypotheses.map(hypothesis => <li key={hypothesis.id}>
        <strong>{hypothesis.id} · {hypothesis.cause}</strong><p>{hypothesis.change}</p>
        <p><code>{hypothesis.predict.metric}</code>：{hypothesis.predict.from ?? '无基线'} → {hypothesis.predict.to}</p>
      </li>)}</ol>
      <h4>相对上一轮的变化</h4><p>{plan.previous ? `上一轮：${plan.previous}` : '首次提交或本计划修订'}</p>
      {changes.length ? <div className="training-diff-scroll"><table aria-label="训练计划变化"><thead><tr><th>字段</th><th>之前</th><th>本轮</th></tr></thead>
        <tbody>{changes.map(change => <tr key={change.field}><th scope="row">{change.field}</th>
          <td><pre>{value(change.before)}</pre></td><td><pre>{value(change.after)}</pre></td></tr>)}</tbody></table></div> : <p>内容无变化。</p>}
      <h4>Agent 理由</h4><p>{plan.rationale}</p>
      <p>已读实验记录：{plan.notebookRead.join(', ') || '尚无历史条目'}</p>
    </section>
    <section aria-label="训练计划访谈记录"><h3>访谈记录</h3><Interview transcript={plan.interview} /></section>
  </div>;
}
