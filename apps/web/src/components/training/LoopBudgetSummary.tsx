import type { BudgetAxis, LoopBudget } from '../../lib/notebooks';
import './notebooks.css';

const amount = (value: number | null | undefined, digits = 2) => value == null ? '未知' : value.toFixed(digits);
const axes: { id: BudgetAxis; label: string; digits: number }[] = [
  { id: 'gpuHours', label: '作业 GPU 小时', digits: 3 }, { id: 'episodes', label: '评估尝试', digits: 0 },
  { id: 'caseRuns', label: 'Agent 题次', digits: 0 }, { id: 'llmUsd', label: 'LLM USD（估算）', digits: 6 },
];

export function LoopBudgetSummary({ budget, review = false }: { budget: LoopBudget; review?: boolean }) {
  return <section className="loop-budget" aria-label="目标闭环预算">
    <header><h3>闭环预算</h3><code>{budget.goalId}</code></header>
    {budget.expired && <p className="loop-budget-warning">目标期限已过，原截止时间保持不变。</p>}
    {review && budget.exhaustedBefore && <p className="loop-budget-warning" role="alert">闭环预算已用尽 · 本次批准仅授权此计划，目标上限保持不变。</p>}
    {review && !budget.exhaustedBefore && budget.exhausted && <p className="loop-budget-warning">本计划将用尽或超出闭环预算。</p>}
    {!review && budget.exhausted && <p className="loop-budget-warning">闭环预算已用尽 · 新计划仍需人工审批。</p>}
    <div className="loop-budget-axes">{axes.filter(axis => axis.id in budget.actual || axis.id in budget.effective).map(({ id: axis, label, digits }) => {
      const maximum = budget.limit?.[axis], effective = budget.effective[axis];
      return <div className="loop-budget-axis" key={axis}><strong>{label}</strong>
        <span>已观测 {amount(budget.actual[axis], digits)} · 未用承诺 {amount(budget.unspentCommitment[axis], digits)}</span>
        {budget.actual[axis] == null && budget.knownActual?.[axis] != null && <small>已知部分 {amount(budget.knownActual[axis], digits)}，其余用量未确认。</small>}
        {maximum != null && maximum > 0 && effective != null && <meter aria-label={`${label}有效容量`} min="0" max={maximum} value={Math.min(maximum, effective)} />}
        <small>有效占用 {amount(effective, digits)} / 上限 {amount(maximum, digits)} · 剩余 {amount(budget.remaining[axis], digits)}</small>
      </div>;
    })}<div className="loop-budget-axis"><strong>墙钟天数</strong><span>{budget.firstApprovedAt == null ? '尚无首次批准' : `已过 ${amount(budget.elapsedDays)} 天`}</span>
      <small>上限 {amount(budget.limit?.days)} 天{budget.deadline != null && ` · 截止 ${new Date(budget.deadline * 1000).toLocaleString()}`}</small>
    </div></div>
    {budget.limit == null && <p className="rsi-note">目标上限未知：原批准的预算或固定输入证据不足。计划中的新声明不会替代旧上限。</p>}
    {budget.authority && <p className="rsi-note">原预算来源：{budget.authority.planId} · r{budget.authority.revision}。计划批准不重置目标上限或截止时间。</p>}
    <p className="rsi-note">用量包含失败与取消的已知消耗；承诺与实际分别展示。{'llmUsd' in budget.actual ? '受管 Agent 的未确认用量保留未知。' : 'LLM 用量未知。'}</p>
  </section>;
}
