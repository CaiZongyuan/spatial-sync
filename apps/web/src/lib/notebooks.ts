import { request } from './api';
import type { Actor } from './protocol';
import type { TrainingPlanDocument } from './training';

export type HypothesisVerdict = 'confirmed' | 'refuted' | 'inconclusive';
export type BudgetAxis = 'gpuHours' | 'episodes' | 'caseRuns' | 'llmUsd';
export type BudgetAmounts = Partial<Record<BudgetAxis, number | null>>;
export interface LoopBudget {
  goalId: string;
  limit: (BudgetAmounts & { days: number }) | null;
  limitSource: string;
  limitReason?: string | null;
  actual: BudgetAmounts;
  knownActual?: BudgetAmounts;
  unspentCommitment: BudgetAmounts;
  effective: BudgetAmounts;
  remaining: BudgetAmounts;
  firstApprovedAt: number | null;
  deadline: number | null;
  elapsedDays: number;
  exhausted: boolean;
  exhaustedBefore?: boolean;
  exhaustedDimensions: string[];
  authority?: { planId: string; revision: number } | null;
  coverage?: Record<string, string>;
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
  status: 'completed' | 'failed' | 'cancelled'; at: string; endedAt: string | null;
  score: { successes: number; episodes: number; score: number; ci95: number[] } | null;
  attemptedEpisodes: number | null;
  reportedDecision: 'baseline' | 'accepted' | 'rejected' | null;
  comparison: { state: 'baseline' | 'comparable' | 'unknown' | 'different' | 'changed_during_execution';
    reason: string | null; conditionsGroup: string; referenceEvaluationId: string | null;
    referenceScore: number | null; remainingGapReduction: number | null; gapReason: string | null };
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
export const listGoals = (signal?: AbortSignal) => request<{ goals: string[] }>('/loops', { signal });
export const getGoalLoop = (goal: string, signal?: AbortSignal) => request<GoalLoop>(`/loops/${encodeURIComponent(goal)}`, { signal });
