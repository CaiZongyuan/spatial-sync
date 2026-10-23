import type { Actor } from './protocol';

export interface TrainingPlanDocument {
  id: string;
  kind: 'training';
  title: string;
  goal: string;
  task: string;
  previous: string | null;
  algorithm: Record<string, unknown>;
  model: Record<string, unknown>;
  hyperparameters: Record<string, unknown>;
  jobs: { seeds: number[] };
  budget: { gpuHours: number; episodes?: number | null };
  evaluation: { scene: string; episodes: number; threshold: number | null };
  rationale: string;
  hypotheses: { id: string; cause: string; change: string; predict: { metric: string; from?: number | null; to: string | number } }[];
  notebookRead: string[];
  interview: string;
  revision: number;
  status: 'draft' | 'submitted' | 'approved' | 'rejected' | 'completed' | 'abandoned';
  requestedBy: Actor;
  createdAtMs: number;
  approvalId: string | null;
  approvalStatus: string | null;
  approvalMode: 'human';
  changes: TrainingPlanChange[];
}
export interface TrainingPlanChange { field: string; before: unknown; after: unknown }
