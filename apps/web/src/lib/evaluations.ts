import { request } from './api';

interface SuiteMetadata {
  id: string;
  title: string;
  contentHash: string;
  scene: string;
  sceneContentHash: string;
  source: string;
  target: string;
  split: 'train';
}

export interface EmbodiedEvaluationSuite extends SuiteMetadata {
  layer: 'embodied';
  implements: string;
  episodes: { id: string; params: Record<string, unknown> }[];
  coverage: Record<string, unknown[]>;
  milestones: string[];
}

export interface AgentEvaluationSuite extends SuiteMetadata {
  layer: 'agent';
  runsPerCase: number;
  cases: { id: string; statement: string }[];
  gradingDimensions: string[];
}

export type EvaluationSuite = EmbodiedEvaluationSuite | AgentEvaluationSuite;

export type EvaluationConditions = Record<string, string | null>;
export interface CandidateIdentity {
  kind: string; id?: string | null; contentHash?: string | null; codeHash?: string | null;
  configHash?: string | null; archiveSha256?: string | null;
}
export interface ProgramExecution {
  kind: 'program'; program: string; programContentHash: string; codeHash: string; configHash: string;
  archiveSha256: string; actionStarts: number; callbackErrors: number;
  callbacks: Partial<Record<'init' | 'accept' | 'start' | 'tick' | 'cancel', number>>;
}
export interface EvaluationScore {
  evaluationId: string;
  suite: string;
  subject: string;
  version: string | number;
  candidateIdentity?: CandidateIdentity | null;
  key: EvaluationConditions;
  conditionsGroup?: string;
  comparisonState?: 'known' | 'unknown';
  split: 'train';
  successes?: number;
  episodes: number;
  score: number;
  ci95: [number, number] | null;
  decision: 'baseline' | 'accepted' | 'rejected' | 'reported';
  reference?: string | null;
  phases?: Record<string, { passed: number; episodes: number; rate: number }>;
  recordings: string[];
  cost?: { gpuHours?: number | null; source?: string; observationBasis?: string; llmUsd?: number | null;
    tokens?: number | null; requests?: number | null;
    costSource?: 'runtime-reported' | 'runtime-estimate' | 'runtime-mixed' | 'unknown'; runtimeRecordIds?: string[] };
  durationSeconds: number;
  at: string;
}

export interface EvaluationRecord {
  layer?: 'agent' | 'embodied';
  evaluationId: string;
  suite: string;
  subject: string;
  split: 'train';
  status: string;
  at: string;
  runtimeHash: string;
  candidateIdentity?: CandidateIdentity | null;
  episodes: { caseId: string; status?: 'starting' | 'running' | 'valid' | 'invalid' | 'cancelled';
    success?: boolean | null; milestones: Record<string, boolean>; reason?: string;
    failedStage?: string | null; invocationId?: string; invocationStatus?: string;
    recordingId?: string; sessionId?: string; cleanupComplete?: boolean | null;
    programExecution?: ProgramExecution | null }[];
  result?: EvaluationScore;
  error?: { code: string };
}

export function suitePath(id: string) {
  const match = /^suite\/([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)$/.exec(id);
  if (!match) throw new Error('无效的评估集版本');
  return `/suites/${encodeURIComponent(match[1])}/${match[2]}`;
}

export const suiteSegment = (id: string) => id.replace(/^suite\//, '');

export async function readEvaluationCenter(suiteId: string | undefined, signal: AbortSignal) {
  const batch = new AbortController();
  const options = { signal: AbortSignal.any([signal, batch.signal]) };
  try {
    const [suites, evaluations, scores] = await Promise.all([
      suiteId ? request<EvaluationSuite>(suitePath(suiteId), options).then(suite => [suite])
        : request<{ suites: EvaluationSuite[] }>('/suites', options).then(value => value.suites),
      request<{ evaluations: EvaluationRecord[] }>('/evaluations', options).then(value => value.evaluations),
      suiteId ? request<{ rows: EvaluationScore[] }>(`${suitePath(suiteId)}/scoreboard`, options).then(value => value.rows)
        : Promise.resolve(undefined),
    ]);
    return { suites, evaluations: evaluations.filter(value => !suiteId || value.suite === suiteId),
      rows: scores ?? evaluations.flatMap(value => value.result ? [value.result] : []).sort((a, b) => a.at.localeCompare(b.at)) };
  } finally {
    batch.abort();
  }
}

export const compareEvaluations = (suite: string, compare: string, against: string, signal: AbortSignal) =>
  request<{ comparison: { scoreDelta: number } }>(`${suitePath(suite)}/scoreboard?${new URLSearchParams({ compare, against })}`, { signal });

// A missing server equivalence marker is conservatively a separate curve.
export function scoreGroups(rows: EvaluationScore[]) {
  const groups = new Map<string, EvaluationScore[]>();
  for (const row of rows) {
    const id = JSON.stringify([row.suite, row.comparisonState === 'known' ? row.conditionsGroup ?? row.evaluationId : row.evaluationId]);
    const group = groups.get(id) ?? [];
    group.push(row); groups.set(id, group);
  }
  return [...groups.values()];
}

export function currentReference(rows: EvaluationScore[]) {
  return [...rows].reverse().find(row => row.comparisonState === 'known' && (row.decision === 'baseline' || row.decision === 'accepted'));
}

export const successRate = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? '未知' : `${(value * 100).toFixed(1)}%`;
export const agentScore = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? '未知' : `${value.toFixed(1)} / 100`;
export const shortVersion = (value: string | number) => String(value).length > 14 ? `${String(value).slice(0, 10)}…` : String(value);
export const evaluationDate = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
};
