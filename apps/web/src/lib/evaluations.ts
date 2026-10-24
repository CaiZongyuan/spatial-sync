import { request } from './api';
import type { AgentCaseRun } from './jobs';

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
interface ScoreBase {
  evaluationId: string;
  suite: string;
  subject: string;
  version: string | number;
  key: EvaluationConditions;
  conditionsGroup?: string;
  split: 'train';
  score: number;
  reference: string | null;
  recordings: string[];
  cost?: { gpuHours?: number | null; llmUsd?: number | null; tokens?: number | null };
  durationSeconds: number;
  at: string;
  planId?: string; planRevision?: number; goalId?: string;
}
export interface EmbodiedEvaluationScore extends ScoreBase {
  layer?: 'embodied'; successes: number; episodes: number;
  ci95: [number, number] | null; decision: 'baseline' | 'accepted' | 'rejected';
  phases: Record<string, { passed: number; episodes: number; rate: number }>;
}
export interface AgentEvaluationScore extends ScoreBase {
  layer: 'agent'; version: number; ci95: null; decision: 'reported';
  runs: number; caseRuns: number; traces: string[];
}
export type EvaluationScore = EmbodiedEvaluationScore | AgentEvaluationScore;

interface EvaluationRecordBase {
  evaluationId: string;
  suite: string;
  subject: string;
  split: 'train';
  status: string;
  at: string;
  runtimeHash?: string;
  result?: EvaluationScore;
  error?: { code: string };
}
export interface EmbodiedEvaluationRecord extends EvaluationRecordBase {
  layer?: 'embodied';
  episodes: { caseId: string; success: boolean; milestones: Record<string, boolean>;
    failedStage: string | null; invocationId: string; invocationStatus: string; recordingId: string }[];
}
export interface AgentEvaluationRecord extends EvaluationRecordBase {
  layer: 'agent'; jobId: string; allocatedCaseRuns: number; attemptedCaseRuns: number; caseRuns: AgentCaseRun[];
}
export type EvaluationRecord = EmbodiedEvaluationRecord | AgentEvaluationRecord;

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
export function scoreGroups<T extends EvaluationScore>(rows: T[]) {
  const groups = new Map<string, T[]>();
  for (const row of rows) {
    const id = JSON.stringify([row.suite, row.subject, row.conditionsGroup ?? row.evaluationId]);
    const group = groups.get(id) ?? [];
    group.push(row); groups.set(id, group);
  }
  return [...groups.values()];
}

export function currentReference(rows: EvaluationScore[]) {
  return [...rows].reverse().find((row): row is EmbodiedEvaluationScore => row.layer !== 'agent' && (row.decision === 'baseline' || row.decision === 'accepted'));
}
export const evaluationScoreLabel = (row: EvaluationScore) => row.layer === 'agent'
  ? `${row.score.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} / 100` : successRate(row.score);

export const successRate = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? '未知' : `${(value * 100).toFixed(1)}%`;
export const shortVersion = (value: string | number) => String(value).length > 14 ? `${String(value).slice(0, 10)}…` : String(value);
export const evaluationDate = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
};
