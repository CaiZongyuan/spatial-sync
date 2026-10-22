import { request } from './api';

export interface EvaluationSuite {
  id: string;
  layer: 'embodied' | 'agent';
  title: string;
  contentHash: string;
  scene: string;
  sceneContentHash: string;
  source: string;
  target: string;
  implements: string;
  split: 'train';
  episodes: { id: string; params: Record<string, unknown> }[];
  coverage: Record<string, unknown[]>;
  milestones: string[];
}

export type EvaluationConditions = Record<string, string | null>;
export interface EvaluationScore {
  evaluationId: string;
  suite: string;
  subject: string;
  version: string | number;
  key: EvaluationConditions;
  conditionsGroup?: string;
  split: 'train';
  successes: number;
  episodes: number;
  score: number;
  ci95: [number, number] | null;
  decision: 'baseline' | 'accepted' | 'rejected';
  reference: string | null;
  phases: Record<string, { passed: number; episodes: number; rate: number }>;
  recordings: string[];
  cost?: { gpuHours?: number | null };
  durationSeconds: number;
  at: string;
}

export interface EvaluationRecord {
  evaluationId: string;
  suite: string;
  subject: string;
  split: 'train';
  status: string;
  at: string;
  runtimeHash: string;
  episodes: { caseId: string; success: boolean; milestones: Record<string, boolean>;
    failedStage: string | null; invocationId: string; invocationStatus: string; recordingId: string }[];
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
  const [suites, evaluations, scores] = await Promise.all([
    suiteId ? request<EvaluationSuite>(suitePath(suiteId), { signal }).then(suite => [suite])
      : request<{ suites: EvaluationSuite[] }>('/suites', { signal }).then(value => value.suites),
    request<{ evaluations: EvaluationRecord[] }>('/evaluations', { signal }).then(value => value.evaluations),
    suiteId ? request<{ rows: EvaluationScore[] }>(`${suitePath(suiteId)}/scoreboard`, { signal }).then(value => value.rows)
      : Promise.resolve(undefined),
  ]);
  return { suites, evaluations: evaluations.filter(value => !suiteId || value.suite === suiteId),
    rows: scores ?? evaluations.flatMap(value => value.result ? [value.result] : []) };
}

export const compareEvaluations = (suite: string, compare: string, against: string, signal: AbortSignal) =>
  request<{ comparison: { scoreDelta: number } }>(`${suitePath(suite)}/scoreboard?${new URLSearchParams({ compare, against })}`, { signal });

// A missing server equivalence marker is conservatively a separate curve.
export function scoreGroups(rows: EvaluationScore[]) {
  const groups = new Map<string, EvaluationScore[]>();
  for (const row of rows) {
    const id = JSON.stringify([row.suite, row.subject, row.conditionsGroup ?? row.evaluationId]);
    const group = groups.get(id) ?? [];
    group.push(row); groups.set(id, group);
  }
  return [...groups.values()];
}

export function currentReference(rows: EvaluationScore[]) {
  return [...rows].reverse().find(row => row.decision === 'baseline' || row.decision === 'accepted');
}

export const successRate = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? '未知' : `${(value * 100).toFixed(1)}%`;
export const shortVersion = (value: string | number) => String(value).length > 14 ? `${String(value).slice(0, 10)}…` : String(value);
export const evaluationDate = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
};
