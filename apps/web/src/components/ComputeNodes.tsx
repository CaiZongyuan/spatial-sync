import { Link } from '@tanstack/react-router';
import type { TrainingJob, TrainingNode } from '../lib/jobs';
import { localTime, megabytes, percentage, releaseEstimate } from '../lib/training-presentation';
import { GPUHistory } from './GPUHistory';

export function ComputeNodes({ nodes, jobs, compact = false }: {
  nodes: TrainingNode[]; jobs: TrainingJob[]; compact?: boolean;
}) {
  if (!nodes.length) return <article className="resource-panel is-unconnected compute-unconnected" aria-label="未接入计算节点">
    <div className="resource-panel-body"><h3>本机计算节点尚未接入</h3>
      <p>节点连接后会显示设备、真实采样和资源租约。</p><p className="resource-note">作业将保留在队列中；当前没有可确认的计算容量。</p></div>
  </article>;
  return <div className={`compute-node-list${compact ? ' is-compact' : ''}`}>{nodes.map(node => {
    const leases = Object.entries(node.leases);
    return <article className={`resource-panel compute-node${node.state === 'offline' ? ' is-unconnected' : ''}`} key={node.id}
      aria-label={`计算节点 ${node.id}`} data-testid={`compute-node-${node.id}`}>
      <header><div><h3>{node.id}</h3><span className={`job-status is-${node.state}`}>{node.state === 'online' ? '已连接' : '已失联'}</span>
        {leases.length > 0 && <span className="resource-tag">{leases.length} 个租约</span>}</div>
        {compact && <Link to="/resources/gpu">查看算力 →</Link>}</header>
      <div className="resource-panel-body"><p className="compute-hardware">{node.hardware}</p>
        <p className="resource-note">最近心跳 {localTime(node.lastSeen)} · {node.cpuAvailable ? '支持 CPU 作业' : '未提供 CPU 算力'}</p>
        {node.state === 'offline' && <p className="resource-note">节点未在时限内上报；已有租约保留至作业清理，不能视为空闲。</p>}
        {!node.gpus.length && <p className="resource-note">节点未报告 GPU 设备。</p>}
        <div className="compute-device-list">{node.gpus.map(gpu => {
          const stale = node.state === 'offline' || gpu.observedAt == null || Date.now() / 1000 - gpu.observedAt > 30;
          const utilization = stale ? null : gpu.utilization, memory = stale ? null : gpu.memoryUsedMb;
          return <div className="compute-device" key={gpu.id}>
          <strong>{gpu.name}</strong><code>{gpu.id}</code>
          <span>使用率 {percentage(utilization)} · 显存 {megabytes(memory)} / {megabytes(gpu.memoryTotalMb)}</span>
          {stale && <small>{node.state === 'offline' ? '节点已失联，当前 GPU 状态未知' : '等待节点提供新的有效采样'}</small>}
          {gpu.unavailableReason && <small>{gpu.unavailableReason}</small>}
          {gpu.observedAt != null && <small>采样于 {localTime(gpu.observedAt)}</small>}
          {!compact && <>{memory != null && gpu.memoryTotalMb != null && <meter className="gpu-memory-meter"
            aria-label={`${gpu.name} 显存使用`} min={0} max={gpu.memoryTotalMb} value={memory} />}
            <GPUHistory gpu={gpu} /></>}
        </div>;
        })}</div>
        <div className="compute-leases">{leases.length ? leases.map(([resource, lease]) => {
          const job = jobs.find(value => value.jobId === lease.jobId);
          const estimate = releaseEstimate(job);
          return <div key={resource} className="compute-lease"><span>{resource === 'cpu' ? 'CPU' : resource} · 租约持有者</span>
            <Link to="/train/jobs/$id" params={{ id: lease.jobId }}>{lease.jobId}</Link>
            {!compact && <><small>开始 {localTime(lease.assignedAt)}</small><span>预计释放 {lease.quarantined ? '未知' : estimate.value}</span>
              <small>{lease.quarantined ? '租约正在隔离清理，释放时间未知' : estimate.reason}</small></>}
          </div>;
        }) : <p className="resource-note">当前没有作业持有资源租约。</p>}</div>
      </div>
    </article>;
  })}</div>;
}
