import type { TrainingPlanDocument, TrainingPlanChange } from '../lib/training';
import { ProgramReview } from './training/ProgramReview';
import '../training-plans.css';

type TrainingContent = Pick<TrainingPlanDocument, 'goal' | 'task' | 'algorithm' | 'model' | 'hyperparameters'
  | 'program' | 'jobs' | 'budget' | 'evaluation' | 'rationale' | 'hypotheses' | 'notebookRead' | 'interview' | 'previous'>;
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

export function TrainingPlanDetails({ plan, changes = [] }: { plan: TrainingContent; changes?: TrainingPlanChange[] }) {
  return <div className="training-plan-details" data-testid="training-plan-details">
    <section aria-label="训练计划内容"><h3>{plan.program ? '脚本程序计划' : '训练计划'}</h3>
      <dl className="training-plan-facts">
        <div><dt>目标</dt><dd>{plan.goal}</dd></div>
        <div><dt>Task 版本</dt><dd><code>{plan.task}</code></dd></div>
        {!plan.program && <><div><dt>算法</dt><dd><pre>{value(plan.algorithm)}</pre></dd></div>
        <div><dt>模型</dt><dd><pre>{value(plan.model)}</pre></dd></div>
        <div><dt>规模与超参</dt><dd><pre>{value(plan.hyperparameters)}</pre></dd></div></>}
        <div><dt>作业种子</dt><dd>{plan.jobs.seeds.join(', ') || '无训练作业'}</dd></div>
        {plan.program ? <div><dt>评估回合预算</dt><dd>{plan.budget.episodes} 回合</dd></div> : <div><dt>GPU 预算</dt><dd>{plan.budget.gpuHours} 小时</dd></div>}
        <div><dt>成功标准</dt><dd>{plan.evaluation.scene} · {plan.evaluation.episodes} 回合
          {plan.evaluation.threshold != null && ` · 至少 ${plan.evaluation.threshold} 次成功`}</dd></div>
      </dl>
      {plan.program && <ProgramReview intent={plan.program} />}
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
