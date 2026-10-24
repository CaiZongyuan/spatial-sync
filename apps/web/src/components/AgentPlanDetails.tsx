import type { AgentPlanDocument } from '../lib/agent-rsi';
import '../training-plans.css';

export function AgentPlanDetails({ plan }: { plan: AgentPlanDocument }) {
  return <div className="training-plan-details" data-testid="agent-plan-details">
    <section><h3>Agent 训练计划</h3><dl className="training-plan-facts">
      <div><dt>目标</dt><dd>{plan.goal} · {plan.goalId}</dd></div>
      <div><dt>Agent / Suite</dt><dd>{plan.agentId}<small>{plan.suite}</small></dd></div>
      <div><dt>版本</dt><dd>Reference v{plan.referenceVersion} → 候选 v{plan.candidateVersion}</dd></div>
      <div><dt>固定运行条件</dt><dd>{plan.runtime.name} · {plan.runtime.version ?? '版本未知'}<small>{plan.runtime.model} · {plan.runtime.thinking ?? '思考等级未知'}</small></dd></div>
      <div><dt>本轮预算</dt><dd>{plan.budget.cases} 题次 · {plan.budget.wallSeconds}s · LLM {plan.budget.llmUsd == null ? '未知' : `$${plan.budget.llmUsd}`}</dd></div>
      <div><dt>Goal 原始预算</dt><dd>{plan.goalBudget.cases} 题次 · {plan.goalBudget.days} 天 · LLM {plan.goalBudget.llmUsd == null ? '未知' : `$${plan.goalBudget.llmUsd}`}</dd></div>
      <div><dt>唯一变更</dt><dd>{plan.change.path}{plan.change.field && ` · ${plan.change.field}`}<small>{plan.change.description}</small></dd></div>
    </dl>
      {plan.budgetException && <p role="status">申请有限预算例外：{plan.budgetException.reason} · 额外 {plan.budgetException.additionalCases} 题次 / {plan.budgetException.additionalDays} 天 / ${plan.budgetException.additionalLlmUsd}。批准时须填写人工理由。</p>}
      <h4>可测量假设</h4><ol>{plan.hypotheses.map(hypothesis => <li key={hypothesis.id}><strong>{hypothesis.id} · {hypothesis.cause}</strong><p>{hypothesis.change}</p><code>{hypothesis.predict.metric} → {hypothesis.predict.to}</code></li>)}</ol>
      <h4>理由</h4><p>{plan.rationale}</p><small>已读 Notebook：{plan.notebookRead.join(', ') || '暂无历史条目'}</small>
    </section><section><h3>访谈记录</h3><pre className="training-interview">{plan.interview}</pre></section>
  </div>;
}
