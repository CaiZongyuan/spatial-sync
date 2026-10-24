import type { AgentRun, RuntimeDeclaration } from './agent-runs';
import type { NotebookHypothesis } from './notebooks';
import type { Actor } from './protocol';
import type { TrainingPlanDocument } from './training';

export interface AgentReference {
  agentId: string; evaluationId: string; harnessVersion: number; harnessHash: string;
  archiveHash: string; suite: string; runtime: RuntimeDeclaration; score: number | null;
  recordedScore?: number; comparisonState?: 'known' | 'unknown'; comparisonReason?: string | null;
  conditionsGroup: string; at: string;
  conditions: Record<string, string | null>;
}
export interface NativeObservation {
  llmUsd: number | null; knownLlmUsd?: number; requests: number | null; tokens: number | null;
  runtimeSessionCount?: number; costSource: string; detailsCoverage?: string;
  observationBasis?: string; observedAt?: string; timeBasis?: string;
}
export interface AgentPlanDocument {
  id: string; kind: 'training'; layer: 'agent'; title: string; goal: string; goalId: string;
  agentId: string; suite: string; runtime: RuntimeDeclaration; revision: number; status: string;
  referenceEvaluationId: string; referenceVersion: number; candidateVersion: number;
  change: { kind: string; path: string; field?: string | null; description: string };
  budget: { cases: number; wallSeconds: number; llmUsd: number | null };
  goalBudget: { cases: number; days: number; llmUsd: number | null };
  budgetException?: { reason: string; additionalCases: number; additionalDays: number; additionalLlmUsd: number } | null;
  hypotheses: TrainingPlanDocument['hypotheses']; notebookRead: string[]; rationale: string; interview: string;
  previous: string | null; requestedBy: Actor; createdAtMs: number;
  approvalId: string | null; approvalStatus: string | null; approvalMode: 'human';
  changes: { field: string }[]; context: { reference: AgentReference; candidateHash: string; candidateArchiveHash: string };
  runId: string | null;
  activation?: { status: string; errorCode?: string } | null;
  result: { decision: string; score: number | null; reason: string | null; application: string;
    referenceAdvanced: boolean; cost: NativeObservation; executionCost: NativeObservation;
    files?: { status: string; targetHash: string }; at: string } | null;
  promotions?: { id: string; status: string; approvalId: string | null }[];
  stop?: { application: string; cleanup: { complete: boolean; blocked: boolean }; errorCode?: string } | null;
}
export interface AgentWorkflow {
  agent: { id: string; title: string; currentVersion: number; referenceVersion: number };
  layer: 'agent'; baselines: AgentReference[]; plans: AgentPlanDocument[];
  recovery: { requestId: string; status: string }[]; auditHealthy: boolean;
}
export interface AgentNotebookEntry {
  entryId: string; evaluationId: string; layer: 'agent'; candidateVersion: number; round: number;
  plan: { id: string; title: string; revision: number; referenceVersion: number; candidateVersion: number };
  score: { score: number; minimum: number; maximum: number; cases: number; runs: number; episodes: number; ci95: null } | null;
  status: string; reportedDecision: string; errorCode: string | null; attemptedCases: number;
  currentDisposition?: string;
  comparison: { state: string; conditionsGroup: string; referenceScore: number | null; reason: string | null;
    remainingGapReduction: number | null; gapReason: string | null };
  hypotheses: NotebookHypothesis[]; cost: NativeObservation; files: { status: string };
}
export interface AgentLoop {
  layer: 'agent'; goalId: string; rounds: AgentNotebookEntry[]; plans: AgentPlanDocument[];
  deployment: { id?: string; status: string; approvalId?: string | null };
  budget: { limits: { cases: number; days: number; llmUsd: number | null } | null;
    attemptedCases: number; committedCases: number; remainingCases: number | null; expired: boolean;
    deadlineAt: string | null; usage: NativeObservation; usdEnforcement: string;
    basis: { planId: string; revision: number; startedAtMs: number } | null;
    approvedExceptions: { planId: string; revision: number; extension: { reason: string; additionalCases: number; additionalDays: number; additionalLlmUsd: number } }[] };
}
export interface AgentPromotionReview {
  id: string; status: string; approvalId: string | null; candidateBelowReference?: boolean; errorCode?: string | null;
  test?: { reference: AgentRun; candidate: AgentRun };
}
export const isAgentPlan = (value: unknown): value is AgentPlanDocument =>
  typeof value === 'object' && value !== null && 'layer' in value && value.layer === 'agent';
export const agentName = (id: string) => encodeURIComponent(id.replace(/^agent\//, ''));
export const agentMean = (score: number | null | undefined) => score == null ? '未知' : `${score.toFixed(1)} / 100`;
