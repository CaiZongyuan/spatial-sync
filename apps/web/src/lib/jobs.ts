import { request } from './api';
import type { Actor } from './protocol';

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export interface JobMetrics {
  iteration?: number;
  elapsedSeconds?: number;
  meanReward?: number | null;
  trainSuccess?: number | null;
  memoryUsedMb?: number | null;
}
export interface TimingProfile {
  key: string;
  iterationSeconds: number;
  setupSeconds: number;
  samples: number;
  measuredAt: number;
  evidence: string;
}
export interface JobArtifact { path: string; size: number; sha256: string }
export interface JobVersions {
  versions: Record<string, string>;
  hardware: { device: 'cpu' | 'cuda'; gpu: { uuid: string; name: string } | null; node: string };
  jobId: string;
  task: string;
  taskContentHash: string;
  scene: string;
  sceneContentHash: string;
  seed: number;
}
export interface TrainingJob {
  jobId: string;
  kind: 'training';
  status: JobStatus;
  planId: string;
  planRevision: number;
  planApprovalId: string;
  task: string;
  taskContentHash: string;
  scene: string;
  sceneContentHash: string;
  assets: { id: string; contentHash: string }[];
  seed: number;
  requestId: string;
  requestedBy: Actor;
  // Job/node timestamps are Unix seconds; Plan documents use separate *Ms fields.
  createdAt: number;
  startedAt: number | null;
  finishedAt: number | null;
  executionDevice: 'cpu' | 'cuda';
  estimatedGpuHours: number | null;
  chargedGpuHours: number;
  estimatedWallSeconds: number | null;
  profileKey: string | null;
  profile: TimingProfile | null;
  approvalId: string | null;
  budgetReserved: boolean;
  nodeId: string | null;
  resourceId: string | null;
  resourceModel: string | null;
  gpuId: string | null;
  leaseId: string | null;
  cancelRequested: boolean;
  reason: string | null;
  metrics: JobMetrics;
  artifacts: JobArtifact[];
  versions?: JobVersions;
  code?: string;
}
export interface NodeLease {
  jobId: string;
  leaseId: string;
  assignedAt: number;
  quarantined: boolean;
}
export interface GPUHistory {
  windowSeconds: number;
  sampleIntervalSeconds: number;
  startedAt: number | null;
  samples: {
    at: number;
    observedAt: number | null;
    utilization: number | null;
    memoryUsedMb: number | null;
    unavailableReason: string | null;
  }[];
}
export interface NodeGPU {
  id: string;
  name: string;
  memoryTotalMb: number | null;
  memoryUsedMb: number | null;
  utilization: number | null;
  observedAt: number | null;
  unavailableReason: string | null;
  history?: GPUHistory;
}
export interface TrainingNode {
  id: string;
  instanceId: string;
  hardware: string;
  state: 'online' | 'offline';
  lastSeen: number;
  reconnectDeadline: number;
  cpuAvailable: boolean;
  gpus: NodeGPU[];
  leases: Record<string, NodeLease>;
  profiles: Record<string, TimingProfile>;
  queue: string[];
}

export async function listJobs(
  filters: { plan?: string; status?: JobStatus; kind?: 'training' } = {},
  signal?: AbortSignal,
): Promise<TrainingJob[]> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
  const suffix = query.size ? `?${query}` : '';
  return (await request<{ jobs: TrainingJob[] }>(`/jobs${suffix}`, { signal })).jobs;
}
export const getJob = (id: string, signal?: AbortSignal): Promise<TrainingJob> =>
  request<TrainingJob>(`/jobs/${encodeURIComponent(id)}`, { signal });
export async function listNodes(signal?: AbortSignal): Promise<TrainingNode[]> {
  return (await request<{ nodes: TrainingNode[] }>('/nodes', { signal })).nodes;
}
