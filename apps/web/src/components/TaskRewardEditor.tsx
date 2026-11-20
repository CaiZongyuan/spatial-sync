import { Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { request } from '../lib/api';
import type { TrainingPlanDocument } from '../lib/training';

type Reward = { weight: number; kind?: string };
interface TaskSource {
  id: string;
  contentHash: string;
  task: { declarative?: { rewards: Reward[]; layerB?: { rewards: Reward[] } | null } | null };
}

export function TaskRewardEditor({ plan, onSaved }: { plan: TrainingPlanDocument; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  return <section className="task-reward-editor">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>调整奖励权重</button>
    {open && <RewardForm key={`${plan.revision}:${plan.task}`} {...{ plan, onSaved }} />}
  </section>;
}

function RewardForm({ plan, onSaved }: { plan: TrainingPlanDocument; onSaved: () => void }) {
  const [source, setSource] = useState<TaskSource>();
  const [weights, setWeights] = useState<number[][]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [jobId, setJobId] = useState<string>();
  const taskPath = /^task\/([a-z0-9-]+)@([1-9][0-9]*)$/.test(plan.task)
    ? `/tasks/${plan.task.slice(5).replace('@', '/')}` : null;

  useEffect(() => {
    if (!taskPath) return;
    const controller = new AbortController();
    void request<TaskSource>(taskPath, { signal: controller.signal }).then(value => {
      if (value.id !== plan.task) throw new Error('读取的 Task 与计划版本不符');
      if (controller.signal.aborted) return;
      setSource(value);
      const spec = value.task.declarative;
      if (spec) setWeights([spec.rewards.map(row => row.weight), (spec.layerB?.rewards ?? []).map(row => row.weight)]);
    }).catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [plan.task, taskPath]);

  if (!taskPath) return <p role="alert">计划缺少精确的 Task 版本。</p>;
  if (!source) return <p role={error ? 'alert' : 'status'}>{error || '正在读取 Task 奖励…'}</p>;
  const spec = source.task.declarative;
  if (!spec) return <p>代码 Task 的奖励由其源包定义。</p>;
  const lists = [spec.rewards, spec.layerB?.rewards ?? []];
  const changed = lists.some((rows, layer) => rows.some((row, index) => row.weight !== weights[layer][index]));
  const seed = plan.jobs.seeds[0];

  async function save() {
    if (!changed || busy || !source) return;
    setBusy(true);
    setError('');
    try {
      const { id: task } = await request<TaskSource>(`${taskPath}/reward-weights`, {
        method: 'POST', headers: { 'X-Actor': 'user:web' },
        body: JSON.stringify({ contentHash: source.contentHash, layerA: weights[0], layerB: weights[1] }),
      });
      const { id, revision, kind, title, goal, goalId, previous, algorithm, model, hyperparameters,
        jobs, budget, evaluation, rationale, hypotheses, notebookRead, interview } = plan;
      await request<TrainingPlanDocument>(`/plans/${encodeURIComponent(id)}/revisions?baseRevision=${revision}`, {
        method: 'POST', headers: { 'X-Actor': 'user:web' },
        body: JSON.stringify({ kind, title, goal, goalId, task, previous, algorithm, model, hyperparameters,
          jobs, budget, evaluation, rationale, hypotheses, notebookRead, interview }),
      });
      onSaved();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function preflight() {
    if (busy || changed || seed == null) return;
    setBusy(true);
    setError('');
    try {
      const job = await request<{ jobId: string }>('/jobs', {
        method: 'POST', headers: { 'X-Actor': 'user:web' },
        body: JSON.stringify({ kind: 'plan_validation', planId: plan.id, planRevision: plan.revision, seed }),
      });
      setJobId(job.jobId);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <form onSubmit={event => { event.preventDefault(); void save(); }}>
    <p>来源 <code>{source.id}</code> <code title={source.contentHash}>{source.contentHash.slice(0, 12)}</code></p>
    {lists.map((rows, layer) => rows.length > 0 && <fieldset key={layer} disabled={busy}>
      <legend>{layer === 0 ? 'Layer A' : 'Layer B'}</legend>
      {rows.map((row, index) => {
        const bound = layer === 0 ? Math.max(10, Math.ceil(Math.abs(row.weight))) : 100;
        return <label key={index}>{row.kind ?? `表达式 ${index + 1}`}
          <input type="range" min={-bound} max={bound} step="any" value={weights[layer][index]}
            onChange={event => {
              const value = Number(event.target.valueAsNumber.toPrecision(6));
              setWeights(current => current.map((values, i) => i === layer
                ? values.map((prior, j) => j === index ? value : prior) : values));
            }} />
          <output>{weights[layer][index]}</output>
        </label>;
      })}
    </fieldset>)}
    <div className="task-reward-actions">
      <button type="submit" disabled={busy || !changed}>保存为计划新修订</button>
      {(plan.status === 'submitted' || plan.status === 'approved') && seed != null &&
        <button type="button" disabled={busy || changed} onClick={() => void preflight()}>发起当前修订预检</button>}
    </div>
    {jobId && <Link to="/train/jobs/$id" params={{ id: jobId }}>查看预检作业 →</Link>}
    {error && <p role="alert">{error}</p>}
  </form>;
}
