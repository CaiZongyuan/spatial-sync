import { request } from './api';
import type { Actor } from './protocol';
import type { AgentPlanBudget } from './training';

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export interface JobMetrics {
  iteration?: number;
  elapsedSeconds?: number;
  meanReward?: number | null;
  trainSuccess?: number | null;
  memoryUsedMb?: number | null;
}
export interface JobMetricSample extends JobMetrics {
  receivedAt: number;
  reportedStatus: JobStatus | 'cleaned';
  reason: string | null;
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
  metricsHistory?: JobMetricSample[];
  artifacts: JobArtifact[];
  versions?: JobVersions;
  code?: string;
}
export interface AgentUsage {
  requests: number | null; failures?: number | null; tokens: number | null; llmUsd: number | null;
  knownRequests: number; knownFailures?: number; knownTokens: number; knownLlmUsd: number;
  reservedRequests: number; reservedTokens: number; reservedUsd: number;
  unknownUsageRequests: number; unknownOutcomeRequests?: number; notForwardedRequests?: number;
  priceRevisions?: number[]; stopReason?: string | null;
  inputTokens?: number | null; outputTokens?: number | null; cacheReadTokens?: number | null; cacheWriteTokens?: number | null;
  knownInputTokens?: number; knownOutputTokens?: number; knownCacheReadTokens?: number; knownCacheWriteTokens?: number;
}
export interface AgentCaseRun {
  caseRunId: string; caseId: string; repetition: number; status: string;
  score?: number | null; dimensions?: Record<string, number>; metrics?: Record<string, number | boolean>;
  traceId?: string; recordingId?: string; elapsedSeconds?: number; error?: { code: string };
}
export interface AgentArtifact extends JobArtifact {
  id: string; url: string; caseRunId: string | null;
  kind: 'platform-trace' | 'sdk-messages' | 'final-state' | 'score' | 'usage';
  source: 'platform' | 'sdk' | 'trusted-transport';
}
export interface AgentRunJob {
  jobId: string; kind: 'agent-run'; layer: 'agent';
  status: JobStatus | 'admitting' | 'cancelling'; reason: string | null;
  requestId: string; requestedBy: Actor; agent: string; harnessVersion: number;
  planId: string; planRevision: number; goalId: string; suite: string; split: 'train'; evaluationId: string;
  model: string; thinking: string; executionDevice: 'cpu'; nodeId: null; placement: 'gateway-local'; gpuHours: number;
  createdAt: number; startedAt: number | null; finishedAt: number | null;
  attemptedCaseRuns: number; allocatedCaseRuns: number; currentCase?: string;
  budget: AgentPlanBudget; allocatedLlmUsd: number; allocatedWallSeconds: number;
  executionSeconds: number | null; cleanupSeconds: number; executionTiming?: string;
  goalDeadline: number | null; cleanupIncomplete?: boolean; usage: AgentUsage;
  caseRuns: AgentCaseRun[]; artifacts: AgentArtifact[];
}
export type ManagedJob = TrainingJob | AgentRunJob;
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

type JobFilters = { plan?: string; status?: JobStatus; kind?: ManagedJob['kind'] };
export function listJobs(filters: JobFilters & { kind: 'training' }, signal?: AbortSignal): Promise<TrainingJob[]>;
export function listJobs(filters: JobFilters & { kind: 'agent-run' }, signal?: AbortSignal): Promise<AgentRunJob[]>;
export function listJobs(filters?: JobFilters, signal?: AbortSignal): Promise<ManagedJob[]>;
export async function listJobs(
  filters: JobFilters = {},
  signal?: AbortSignal,
): Promise<ManagedJob[]> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
  const suffix = query.size ? `?${query}` : '';
  return (await request<{ jobs: ManagedJob[] }>(`/jobs${suffix}`, { signal })).jobs;
}
export const getJob = (id: string, signal?: AbortSignal): Promise<ManagedJob> =>
  request<ManagedJob>(`/jobs/${encodeURIComponent(id)}`, { signal });
export const cancelJob = (id: string): Promise<ManagedJob> => request(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
export async function listNodes(signal?: AbortSignal): Promise<TrainingNode[]> {
  return (await request<{ nodes: TrainingNode[] }>('/nodes', { signal })).nodes;
}
