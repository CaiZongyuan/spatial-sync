import { Link } from '@tanstack/react-router';
import { useTrainingResources } from '../lib/use-training-resources';
import { jobReason, localTime } from '../lib/training-presentation';
import { ComputeNodes } from './ComputeNodes';
import { PlatformOverview } from './PlatformOverview';
import '../resources.css';
import '../training-resources.css';

export function ComputeResources() {
  const result = useTrainingResources();
  const queued = result.jobs?.filter(job => job.status === 'queued').sort((a, b) => a.createdAt - b.createdAt);
  return <main className="home compute-page"><PlatformOverview page="resources" nodeCount={result.nodes?.length}>
    <section aria-labelledby="compute-nodes-title"><div className="training-section-heading"><h2 id="compute-nodes-title">计算节点</h2>
      <button onClick={result.refresh}>刷新资源</button></div>
      {result.nodesError && <p className="resource-error" role="alert">节点刷新失败：{result.nodesError}。{result.nodes && '保留最近一次结果。'}</p>}
      {result.nodes ? <ComputeNodes nodes={result.nodes} jobs={result.jobs ?? []} /> :
        <p className="resource-note" role="status">{result.nodesError ? '节点数据暂不可用。' : '正在读取节点…'}</p>}
    </section>
    <section className="resource-panel compute-queue" aria-labelledby="compute-queue-title"><header><h2 id="compute-queue-title">资源等待队列</h2>
      <Link to="/train">查看训练作业 →</Link></header><div className="resource-panel-body">
      {result.jobsError && <p className="resource-error" role="alert">队列刷新失败：{result.jobsError}。{result.jobs && '保留最近一次结果。'}</p>}
      {!result.jobs ? <p className="resource-note">{result.jobsError ? '队列暂不可用。' : '正在读取队列…'}</p> : queued?.length ?
        <div className="resource-table-scroll"><table aria-label="资源等待队列"><thead><tr><th>作业</th><th>计划 / Task</th><th>所需设备</th><th>等待原因</th><th>提交时间</th></tr></thead>
          <tbody>{queued.map(job => <tr key={job.jobId}><td><Link to="/train/jobs/$id" params={{ id: job.jobId }}><code>{job.jobId}</code></Link></td>
            <td><Link to="/plans/$planId" params={{ planId: job.planId }}>{job.planId}</Link><small>{job.task}</small></td>
            <td>{job.executionDevice === 'cpu' ? 'CPU' : 'GPU'}</td><td>{jobReason(job.reason) ?? '等待节点分配资源'}</td><td>{localTime(job.createdAt)}</td></tr>)}</tbody></table></div> :
        <p className="resource-note">当前没有排队作业。</p>}
    </div></section>
  </PlatformOverview></main>;
}
