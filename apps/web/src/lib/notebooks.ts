import { request } from './api';
import type { Actor } from './protocol';
import type { TrainingPlanDocument } from './training';
import type { CandidateIdentity } from './evaluations';

export type HypothesisVerdict = 'confirmed' | 'refuted' | 'inconclusive';
export interface LoopBudget {
  goalId: string;
  limit: { gpuHours: number; episodes: number; days: number; llmUsd?: number | null } | null;
  limitSource: string;
  limitReason?: string | null;
  actual: { gpuHours: number; episodes: number };
  unspentCommitment: { gpuHours: number; episodes: number };
  effective: { gpuHours: number; episodes: number };
  remaining: { gpuHours: number | null; episodes: number | null };
  firstApprovedAt: number | null;
  deadline: number | null;
  elapsedDays: number;
  exhausted: boolean;
  exhaustedBefore?: boolean;
  exhaustedDimensions: string[];
}
export interface NotebookHypothesis {
  definition: TrainingPlanDocument['hypotheses'][number];
  automatic: { verdict: HypothesisVerdict; metric: string; observed: number | null; ci95: number[] | null; reason: string };
  effectiveVerdict: HypothesisVerdict;
  overrides: { requestId: string; verdict: HypothesisVerdict; actor: Actor; atMs: number; note: string }[];
}
export interface NotebookEntry {
  entryId: string; evaluationId: string; goalId: string; round?: number;
  plan: { id: string; revision: number; task: string; goalId: string; goal: string; title: string; changedFields: string[]; targetSuccessRate?: number | null };
  suite: string; subject: string; candidateVersion: string | null;
  candidateIdentity?: CandidateIdentity | null;
  status: 'completed' | 'failed' | 'cancelled'; at: string; endedAt: string | null;
  score: { successes: number; episodes: number; score: number; ci95: number[] } | null;
  attemptedEpisodes: number | null;
  reportedDecision: 'baseline' | 'accepted' | 'rejected' | null;
  comparison: { state: 'baseline' | 'comparable' | 'unknown' | 'different' | 'changed_during_execution';
    reason: string | null; conditionsGroup: string; referenceEvaluationId: string | null;
    referenceScore: number | null; remainingGapReduction: number | null; gapReason: string | null };
  recordedComparison?: NotebookEntry['comparison'];
  deployment: { status: string; approvalId?: string | null };
  hypotheses: NotebookHypothesis[]; recordings: string[]; errorCode: string | null;
  knownJobGpuHours: number | null; costCoverage: string;
}
export interface GoalLoop {
  goalId: string; rounds: NotebookEntry[]; budget: LoopBudget;
  reference: NotebookEntry['comparison'] | null;
  candidate: { evaluationId: string; subject: string; version: string | null; score: NotebookEntry['score']; reportedDecision: NotebookEntry['reportedDecision'] } | null;
  deployment: NotebookEntry['deployment'];
  defaultProgram: { program: string; scene: string | null; evaluationId: string } | null;
}
export const listGoals = (signal?: AbortSignal, layer?: 'embodied' | 'agent') =>
  request<{ goals: string[] }>(`/loops${layer ? `?layer=${layer}` : ''}`, { signal });
export const getGoalLoop = (goal: string, signal?: AbortSignal) => request<GoalLoop>(`/loops/${encodeURIComponent(goal)}`, { signal });
