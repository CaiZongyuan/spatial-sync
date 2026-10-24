import { request } from './api';
import type { EvaluationScore } from './evaluations';
import type { RuntimeUsage } from './runtime-records';

export interface RuntimeDeclaration { name: string; version: string | null; model: string; thinking: string }
export interface AgentSummary { id: string; title: string; currentVersion: number; referenceVersion: number }
export interface HarnessVersion { version: number; contentHash: string; archiveHash: string; decision: string }
export interface AgentCase {
  caseRunId: string; caseId: string; repeat: number;
  status: 'waiting' | 'running' | 'completed' | 'invalid' | 'cancelled';
  sessionId?: string; nativeSessionId?: string; runtimeRecordId?: string; runtimeRecordHash?: string;
  score?: number; dimensions?: Record<string, number>; recordingId?: string;
  artifacts: { id: string; kind: string; url: string; sha256: string; size: number }[];
  usage?: RuntimeUsage & { observationBasis: 'completion-snapshot' };
  error?: { code: string };
}
export interface AgentRun {
  id: string; evaluationId: string; layer: 'agent'; agentId: string; subject: string; harnessVersion: number;
  suite: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  purpose: 'train' | 'verification'; runtime: RuntimeDeclaration; requestId: string;
  createdAt: string; at: string; deadlineAt: string; cases: AgentCase[];
  pins: { harnessHash: string; archiveHash: string; skills: { name: string; sha256: string }[];
    suiteContentHash: string; scene: string; sceneContentHash: string; source: string };
  cleanup: { complete: boolean; blocked: boolean };
  result?: EvaluationScore;
  error?: { code: string };
}
export interface AgentRunInput {
  requestId: string; suite: string; harnessVersion: number; runtime: RuntimeDeclaration;
  purpose: 'train' | 'verification'; caseId?: string; wallSeconds: number;
}

const agentPath = (id: string) => `/agents/${encodeURIComponent(id.replace(/^agent\//, ''))}`;
export const listAgents = async (signal: AbortSignal) =>
  (await request<{ agents: AgentSummary[] }>('/agents', { signal })).agents;
export const listHarnessVersions = async (agent: string, signal: AbortSignal) =>
  (await request<{ versions: HarnessVersion[] }>(`${agentPath(agent)}/versions`, { signal })).versions;
export const listAgentRuns = async (signal: AbortSignal) =>
  (await request<{ runs: AgentRun[] }>('/agent-runs', { signal })).runs;
export const createAgentRun = (agent: string, input: AgentRunInput, signal: AbortSignal) =>
  request<AgentRun>(`${agentPath(agent)}/runs`, { method: 'POST', body: JSON.stringify(input), signal });
export const cancelAgentRun = (id: string, signal: AbortSignal) =>
  request<AgentRun>(`/agent-runs/${encodeURIComponent(id)}/cancel`, { method: 'POST', signal });

export const agentRunStatus = { queued: '排队中', running: '等待外部 runtime', completed: '已完成', failed: '无效 / 未完成', cancelled: '已取消' };
export const agentCaseStatus = { waiting: '等待交接', running: '等待完成信号', completed: '已评分', invalid: '基础设施无效', cancelled: '已取消' };
