import { Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { request } from '../lib/api';
import { getJob, type TrainingJob } from '../lib/jobs';
import { remainingSeconds } from '../lib/training-presentation';
import type { TrainingPlanDocument } from '../lib/training';
import { TrainingPlanDetails } from './TrainingPlanDetails';
import '../job-detail.css';

const statuses = { queued: '排队中', running: '训练中', succeeded: '已完成', failed: '失败', cancelled: '已取消' };
const value = (number: number | null | undefined, digits = 2) => number == null ? '—' : number.toFixed(digits);

export function JobDetailPage({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<TrainingJob>();
  const [error, setError] = useState<string>();
  const [retry, setRetry] = useState(0);
  const [plan, setPlan] = useState<TrainingPlanDocument>();
  const [planError, setPlanError] = useState<string>();
  const [logs, setLogs] = useState<string[]>([]);
  const [logsComplete, setLogsComplete] = useState(false);
  const [logsError, setLogsError] = useState<string>();

  useEffect(() => {
    let disposed = false, pending: AbortController | undefined;
    setJob(undefined); setError(undefined);
    const refresh = async () => {
      if (pending) return;
      const controller = new AbortController(); pending = controller;
      try { const result = await getJob(jobId, controller.signal); if (!disposed) { setJob(result); setError(undefined); } }
      catch (failure) { if (!disposed) setError((failure as Error).message); }
      finally { pending = undefined; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 2000);
    return () => { disposed = true; clearInterval(timer); pending?.abort(); };
  }, [jobId, retry]);

  useEffect(() => {
    setPlan(undefined); setPlanError(undefined);
    if (!job) return;
    const controller = new AbortController();
    void request<TrainingPlanDocument>(`/plans/${encodeURIComponent(job.planId)}?revision=${job.planRevision}`, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setPlan(result); })
      .catch(failure => { if (!controller.signal.aborted) setPlanError((failure as Error).message); });
    return () => controller.abort();
  }, [job?.planId, job?.planRevision]);

  useEffect(() => {
    const controller = new AbortController();
    setLogs([]); setLogsError(undefined); setLogsComplete(false);
    const follow = async () => {
      let cursor = 0;
      try {
        while (!controller.signal.aborted) {
          const result = await request<{ lines: string[]; cursor: number; complete: boolean }>(
            `/jobs/${encodeURIComponent(jobId)}/logs?after=${cursor}&timeoutSeconds=20`, { signal: controller.signal });
          if (controller.signal.aborted) return;
          if (result.lines.length) setLogs(previous => [...previous, ...result.lines].slice(-2000));
          cursor = result.cursor;
          if (result.complete) { setLogsComplete(true); return; }
        }
      } catch (failure) { if (!controller.signal.aborted) setLogsError((failure as Error).message); }
    };
    void follow();
    return () => controller.abort();
  }, [jobId, retry]);

  if (!job || job.jobId !== jobId) return <section className="job-detail"><header className="page-header"><h1>训练作业详情</h1></header>
    {error ? <div role="alert">{error} <button onClick={() => setRetry(current => current + 1)}>重试</button></div>
      : <p role="status" aria-busy="true">正在读取作业…</p>}</section>;
  const iterations = plan?.hyperparameters.max_iterations;
  const total = typeof iterations === 'number' ? iterations : undefined;
  const elapsed = job.metrics.elapsedSeconds;
  const remaining = remainingSeconds(job.status, job.estimatedWallSeconds, elapsed);
  const checkpoints = job.artifacts.filter(artifact => artifact.path.endsWith('.pt'));
  return <main className="job-detail" data-testid="job-detail">
    <header className="page-header"><div><p className="eyebrow">TRAINING JOB</p><h1>训练作业详情</h1>
      <p><code>{job.jobId}</code> · {statuses[job.status]}</p></div>
      <Link to="/train">返回训练</Link></header>
    {error && <p role="alert">{error} <button onClick={() => setRetry(current => current + 1)}>重试</button></p>}
    <div className="job-stat-strip" aria-label="作业标题指标">
      <div><span>Iteration</span><strong>{job.metrics.iteration ?? '—'} / {total ?? '—'}</strong></div>
      <div><span>Mean reward</span><strong>{value(job.metrics.meanReward, 3)}</strong></div>
      <div><span>Train success</span><strong>{job.metrics.trainSuccess == null ? '—' : `${value(job.metrics.trainSuccess * 100, 1)}%`}</strong></div>
      <div><span>已用 / 预计剩余</span><strong>{value(elapsed, 0)}s / {value(remaining, 0)}s</strong></div>
    </div>
    <div className="job-detail-columns"><div>
      <section className="panel"><h2>检查点</h2>{checkpoints.length ? <table><thead><tr><th>文件</th><th>大小</th><th>SHA256</th></tr></thead>
        <tbody>{checkpoints.map(artifact => <tr key={artifact.path}><td><code>{artifact.path}</code></td><td>{artifact.size.toLocaleString()} B</td>
          <td><code title={artifact.sha256}>{artifact.sha256.slice(0, 12)}</code></td></tr>)}</tbody></table>
        : <p>{job.status === 'queued' || job.status === 'running' ? '等待训练产物。' : '该作业未产生检查点。'}</p>}</section>
      <section className="panel"><h2>日志 <small>最近最多 2000 行 · {logsComplete ? '已读至末尾' : '跟随中'}</small></h2>
        {logsError && <p role="alert">{logsError}</p>}<pre className="job-log" aria-label="作业日志">{logs.join('\n') || '等待节点日志。'}</pre></section>
      <section className="panel"><h2>作业产物</h2>{job.artifacts.length ? <ul>{job.artifacts.map(artifact =>
        <li key={artifact.path}><code>{artifact.path}</code> · {artifact.size.toLocaleString()} B</li>)}</ul> : <p>暂无已上传产物。</p>}</section>
    </div><aside>
      <section className="panel"><h2>作业检查器</h2><dl>
        <dt>状态</dt><dd>{statuses[job.status]}</dd><dt>Task</dt><dd>{job.task}</dd>
        <dt>场景</dt><dd>{job.scene}</dd><dt>节点 / 设备</dt><dd>{job.nodeId ?? '—'} / {job.executionDevice}</dd>
        <dt>Seed</dt><dd>{job.seed}</dd><dt>GPU 型号</dt><dd>{job.resourceModel ?? '—'}</dd>
        <dt>训练端 Newton</dt><dd>{job.versions?.versions.newton ?? '—'}</dd>
        <dt>说明</dt><dd>{job.reason ?? '—'}</dd></dl>
        {job.approvalId && <Link to="/approvals">查看作业审批</Link>}</section>
      <section className="panel"><h2>批准的训练计划 · 修订 {job.planRevision}</h2>
        {planError ? <p role="alert">{planError}</p> : plan ? <TrainingPlanDetails plan={plan} changes={plan.changes} />
          : <p role="status">正在读取固定计划修订…</p>}</section>
    </aside></div>
  </main>;
}
