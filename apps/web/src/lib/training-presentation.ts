import type { JobStatus, TrainingJob } from './jobs';

export const jobStatusLabels: Record<JobStatus, string> = {
  queued: '排队中', running: '运行中', succeeded: '已完成', failed: '失败', cancelled: '已取消',
};

const reasons: Record<string, string> = {
  estimate_unavailable: '等待同配置的实测耗时', pending_approval: '等待超预算审批',
  approval_denied: '超预算申请未获批准', node_unavailable: '计算节点未接入',
  gpu_busy: 'GPU 租约被占用', session_gpu_busy: 'GPU 正由交互会话使用',
  node_starting: '节点正在准备作业', node_lost: '节点已失联', cancelling: '正在停止作业',
  cancelled: '已取消', plan_not_approved: '训练计划尚未获批',
};
export const jobReason = (reason: string | null | undefined) => reason ? reasons[reason] ?? reason : undefined;
export const numberOrUnknown = (value: number | null | undefined, digits = 2) =>
  value == null || !Number.isFinite(value) ? '未知' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
export const percentage = (value: number | null | undefined) => value == null ? '未知' : `${numberOrUnknown(value, 1)}%`;
export const megabytes = (value: number | null | undefined) => value == null ? '未知' : `${numberOrUnknown(value, 0)} MiB`;
export const localTime = (seconds: number | null | undefined) => seconds == null ? '未知' :
  new Date(seconds * 1000).toLocaleString('zh-CN', { hour12: false });
export function elapsed(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return '未知';
  const minutes = Math.floor(seconds / 60), remainder = Math.floor(seconds % 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分` : `${minutes} 分 ${remainder} 秒`;
}
export function remainingSeconds(status: JobStatus, estimate: number | null | undefined, used: number | null | undefined) {
  if (status === 'succeeded' || status === 'failed' || status === 'cancelled') return 0;
  if (estimate == null || used == null || !Number.isFinite(estimate) || !Number.isFinite(used)) return null;
  return Math.max(0, estimate - used);
}
export function releaseEstimate(job: TrainingJob | undefined, now = Date.now() / 1000) {
  if (!job || job.startedAt == null || job.estimatedWallSeconds == null)
    return { value: '未知', reason: jobReason(job?.reason) ?? '尚无已开始作业的实测耗时估计' };
  const at = job.startedAt + job.estimatedWallSeconds;
  return at <= now ? { value: '未知', reason: '作业已超过预计时长，等待实际释放' } :
    { value: localTime(at), reason: '按同配置实测耗时估计，实际释放以节点回报为准' };
}
