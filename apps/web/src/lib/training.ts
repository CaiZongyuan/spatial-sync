import type { Actor } from './protocol';
import type { LoopBudget } from './notebooks';

interface TrainingIntent {
  id: string;
  kind: 'training';
  title: string;
  goal: string;
  goalId?: string | null;
  previous: string | null;
  rationale: string;
  hypotheses: { id: string; cause: string; change: string; predict: { metric: string; from?: number | null; to: string | number } }[];
  notebookRead: string[];
  interview: string;
  revision: number;
  status: 'draft' | 'submitted' | 'approved' | 'rejected' | 'completed' | 'abandoned';
}
export interface EmbodiedTrainingPlanContent extends TrainingIntent {
  layer?: 'embodied';
  task: string;
  algorithm: Record<string, unknown>;
  model: Record<string, unknown>;
  hyperparameters: Record<string, unknown>;
  jobs: { seeds: number[] };
  budget: { gpuHours: number; episodes?: number | null };
  evaluation: { scene: string; episodes: number; threshold: number | null };
}
export interface AgentPlanBudget {
  caseRuns: number; requests: number; tokens: number; llmUsd: number;
  wallSeconds: number; maxOutputTokensPerRequest: number;
}
export interface AgentTrainingPlanContent extends TrainingIntent {
  layer: 'agent';
  goalId: string;
  agent: { id: string; harnessVersion: number };
  suite: string;
  split: 'train';
  model: { ref: string; thinking: string };
  credentialRef: string;
  executionProfile: 'lw.agent-restricted/1';
  budget: AgentPlanBudget;
  goalBudget: { caseRuns: number; llmUsd: number; days: number };
}
export interface AgentFrozenInputs {
  caseRunsRequired: number;
  immutablePlanInputHash: string;
  harness: { harnessHash: string; archiveHash: string; skills: { name: string; sha256: string }[] };
  suite: { id: string; contentHash: string; scene: string; source: string; runsPerCase: number;
    cases: { id: string; statementHash: string }[] };
  adapter: { contextTokens: number; enforcement: Record<string, string> };
  priceQuote: { revision: number; rates: Record<string, number | null> };
}
export type TrainingPlanContent = EmbodiedTrainingPlanContent | AgentTrainingPlanContent;
interface PlanMetadata {
  requestedBy: Actor;
  createdAtMs: number;
  approvalId: string | null;
  approvalStatus: string | null;
  approvalMode: 'human';
  changes: TrainingPlanChange[];
  loopBudget?: LoopBudget;
  frozenInputs?: AgentFrozenInputs;
}
export type TrainingPlanDocument = TrainingPlanContent & PlanMetadata;
export type EmbodiedTrainingPlanDocument = EmbodiedTrainingPlanContent & PlanMetadata;
export type AgentTrainingPlanDocument = AgentTrainingPlanContent & PlanMetadata;
export interface TrainingPlanChange { field: string; before: unknown; after: unknown }
