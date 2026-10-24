import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { useContext } from 'react';
import { ApprovalInboxContext } from '../lib/approvals';
import type { JobStatus } from '../lib/jobs';
import { usePlansList } from '../lib/plans/hooks';
import type { TrainingPlanDocument } from '../lib/training';
import { elapsed, jobReason, jobStatusLabels, numberOrUnknown, percentage } from '../lib/training-presentation';
import { useTrainingResources } from '../lib/use-training-resources';
import { ApprovalCard } from './ApprovalsPage';
import { ComputeNodes } from './ComputeNodes';
import { GoalNotebookPanel } from './training/GoalNotebookPanel';
import '../resources.css';
import '../training-resources.css';

function TrainingStages() {
  return <nav className="training-stages" aria-label="具身层阶段">
    <Link to="/assets"><small>01 · 素材</small><strong>素材库</strong><span>查看可用素材 →</span></Link>
    <Link to="/scenes"><small>02 · 场景</small><strong>场景版本</strong><span>选择物理世界 →</span></Link>
    <Link to="/plans"><small>03 · 计划</small><strong>计划与审批</strong><span>确认目标与预算 →</span></Link>
    <Link to="/train" hash="jobs" aria-current="step"><small>04 · 作业</small><strong>训练作业</strong><span>当前阶段 ↓</span></Link>
    <Link to="/eval"><small>05 · 评估</small><strong>评估中心</strong><span>查看评估结果 →</span></Link>
    <Link to="/train" hash="policies"><small>06 · 策略</small><strong>策略版本</strong><span>尚未接入 ↓</span></Link>
  </nav>;
}

export function TrainPage() {
  const resources = useTrainingResources();
  const plans = usePlansList();
  const approvals = useContext(ApprovalInboxContext);
  const search = useSearch({ from: '/train' }), navigate = useNavigate();
  const status = search.status ?? '', query = search.q ?? '';
  const jobs = resources.jobs?.filter(job => (!status || job.status === status)
    && `${job.jobId} ${job.task} ${job.planId}`.toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => b.createdAt - a.createdAt);
  const trainingPlans = plans.plans?.filter((plan): plan is TrainingPlanDocument => plan.kind === 'training');
  const pending = approvals.inbox?.approvals.filter(record => record.subject === 'plan'
    && record.status === 'pending' && record.details.plan?.kind === 'training');
  return <main className="training-page" data-testid="training-page"><header className="training-heading">
    <div><span className="eyebrow">LAB TRAINING</span><h1>训练</h1><p>从已批准的计划跟进作业、资源与结果。</p></div>
    <div className="training-layer"><span aria-current="page">具身层 · {search.goal?.replace(/^goal\//, '') ?? '机器人训练'}</span>
      <button disabled title="Agent 层作业尚未接入">Agent 层 · 尚未接入</button></div>
  </header><TrainingStages />
    <GoalNotebookPanel goal={search.goal} onGoalChange={goal => void navigate({ to: '/train', search: { ...search, goal }, resetScroll: false })} />
    <section className="training-job-section resource-panel" id="jobs" aria-label="训练作业">
      <header><div><h2>训练作业</h2><span className="resource-tag">{resources.jobs?.length ?? '—'} 个作业</span>
        <span className="resource-tag">{resources.jobs?.filter(job => job.status === 'running').length ?? '—'} 个运行中</span></div>
        <button onClick={resources.refresh}>刷新作业与节点</button></header>
      <div className="resource-panel-body"><div className="training-filters">
        <label>搜索<input aria-label="搜索训练作业" placeholder="作业、Task 或计划" value={query}
          onChange={event => void navigate({ to: '/train', search: { ...search, q: event.target.value || undefined }, resetScroll: false })} /></label>
        <label>状态<select aria-label="筛选作业状态" value={status}
          onChange={event => void navigate({ to: '/train', search: { ...search, status: event.target.value as JobStatus || undefined }, resetScroll: false })}>
          <option value="">全部状态</option>{Object.entries(jobStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label></div>
        {resources.jobsError && <p className="resource-error" role="alert">作业刷新失败：{resources.jobsError}。{resources.jobs && '当前保留最近一次结果。'}</p>}
        {!resources.jobs ? <p className="resource-note" role="status">{resources.jobsError ? '作业数据暂不可用。' : '正在读取训练作业…'}</p> :
          jobs?.length ? <div className="resource-table-scroll"><table className="training-job-table" aria-label="训练作业表">
            <thead><tr><th>作业 / Task</th><th>状态</th><th>训练计划</th><th>节点 / 设备</th><th>迭代进度</th><th className="numeric">平均奖励</th><th className="numeric">训练成功率</th><th>已用时间</th></tr></thead>
            <tbody>{jobs.map(job => {
              const plan = trainingPlans?.find(plan => plan.id === job.planId && plan.revision === job.planRevision);
              const maximum = plan?.hyperparameters.max_iterations;
              return <tr key={job.jobId}><td><Link to="/train/jobs/$id" params={{ id: job.jobId }}><code>{job.jobId}</code></Link><small>{job.task}</small></td>
                <td><span className={`job-status is-${job.status}`}>{jobStatusLabels[job.status]}</span>
                  {jobReason(job.reason) && <small>{jobReason(job.reason)}</small>}</td>
                <td><Link to="/plans/$planId" params={{ planId: job.planId }}>{job.planId}</Link><small>固定修订 r{job.planRevision}</small></td>
                <td>{job.nodeId ?? '等待分配'}<small>{job.executionDevice === 'cpu' ? 'CPU' : job.resourceModel ?? job.resourceId ?? 'GPU 尚未分配'}</small></td>
                <td>{numberOrUnknown(job.metrics.iteration, 0)}{typeof maximum === 'number' && maximum > 0 ? ` / ${maximum}` : ''}
                  <small>{job.metrics.iteration == null ? '等待首次心跳' : typeof maximum !== 'number' ? '总迭代数尚未读取' : '次更新'}</small></td>
                <td className="numeric">{numberOrUnknown(job.metrics.meanReward, 4)}</td>
                <td className="numeric">{percentage(job.metrics.trainSuccess == null ? null : job.metrics.trainSuccess * 100)}</td>
                <td>{elapsed(job.metrics.elapsedSeconds)}</td></tr>;
            })}</tbody></table></div> : <p className="resource-note">{resources.jobs.length ? '没有符合筛选的作业。' :
              <>尚无训练作业。<Link to="/plans">查看计划与审批 →</Link></>}</p>}
        <p className="resource-note">训练成功率来自节点回报；未提供的指标显示未知。平台评估结果在评估中心查看。</p>
      </div>
    </section>
    <div className="training-lower-grid"><section className="training-plan-overview" aria-labelledby="training-plan-title">
      <div className="training-section-heading"><h2 id="training-plan-title">训练计划与审批</h2><Link to="/approvals">打开待审批 →</Link></div>
      {approvals.error && <p className="resource-error" role="alert">审批暂不可用：{approvals.error}</p>}
      {!approvals.inbox ? <p className="resource-note">正在读取审批…</p> : pending?.length ? pending.map((record, index) =>
        <details className="training-approval" key={record.approvalId} open={index === 0}>
          <summary>{record.title}<span>待审批 · 假设与访谈</span></summary>
          <ApprovalCard record={record} canDecide={approvals.inbox?.role === 'operator' && !approvals.error} />
        </details>) : <p className="resource-note">没有待处理的训练计划审批。</p>}
      {plans.error && <p className="resource-error" role="alert">计划暂不可用：{plans.error} <button onClick={plans.retry}>重试</button></p>}
      {trainingPlans && trainingPlans.length > 0 && <div className="training-plan-links"><h3>已提交的训练计划</h3>{trainingPlans.map(plan =>
        <Link to="/plans/$planId" params={{ planId: plan.id }} key={plan.id}><strong>{plan.title}</strong><span>{plan.task} · r{plan.revision}</span></Link>)}</div>}
    </section><section aria-labelledby="training-node-title"><div className="training-section-heading"><h2 id="training-node-title">计算节点</h2><Link to="/resources/gpu">全部算力 →</Link></div>
      {resources.nodesError && <p className="resource-error" role="alert">节点刷新失败：{resources.nodesError}</p>}
      {resources.nodes ? <ComputeNodes compact nodes={resources.nodes} jobs={resources.jobs ?? []} /> :
        <p className="resource-note">{resources.nodesError ? '节点数据暂不可用。' : '正在读取节点…'}</p>}
    </section></div>
    <section className="resource-panel training-unavailable" id="policies" aria-labelledby="training-policies-title"><div className="resource-panel-body">
      <h2 id="training-policies-title">策略提升尚未接入</h2><p>实验记录与 RSI 成绩已在本页展示。策略创建、运行与部署提升将在对应流程上线后显示。</p>
      <Link to="/eval">查看评估中心 →</Link></div></section>
  </main>;
}
