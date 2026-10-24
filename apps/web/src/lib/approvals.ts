import { createContext, useEffect, useState } from 'react';
import { request } from './api';
import type { DeletionDetails } from './library-deletions';
import type { TrainingPlanChange, TrainingPlanContent, AgentFrozenInputs } from './training';
import type { OperationPlan } from './protocol';
import type { LoopBudget } from './notebooks';

export type ApprovalActor = { kind: 'user' | 'cli' | 'agent'; name: string };
export type ApprovalDecision = 'approve' | 'deny' | 'revoke';
export interface ApprovalRecord {
  approvalId: string;
  subject: 'invocation' | 'plan' | 'policy' | 'job' | 'deletion';
  subjectId: string;
  sessionId: string | null;
  title: string;
  details: { entityKey?: string; params?: Record<string, unknown>; plan?: TrainingPlanContent | OperationPlan; revision?: number; changes?: TrainingPlanChange[]; estimatedGpuHours?: number; remainingGpuHours?: number; planId?: string; planRevision?: number; loopBudget?: LoopBudget; frozenInputs?: AgentFrozenInputs } & Partial<DeletionDetails>;
  requestedBy: ApprovalActor;
  createdAtMs: number;
  status: 'pending' | 'approved' | 'denied' | 'revoked' | 'expired' | 'unavailable';
  decisions: { decision: ApprovalDecision; actor: ApprovalActor; atMs: number; note: string }[];
  canRevoke: boolean;
  invocation: { status: string; startedSimTime: number | null } | null;
}
export interface ApprovalInbox { approvals: ApprovalRecord[]; pendingCount: number; role: 'operator' | 'viewer' }
export interface ApprovalInboxState { inbox?: ApprovalInbox; error?: string }
export const ApprovalInboxContext = createContext<ApprovalInboxState>({});

const changed = 'lab:approvals-changed';

export function useApprovalInbox(enabled = true) {
  const [inbox, setInbox] = useState<ApprovalInbox>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let controller: AbortController | undefined;
    const refresh = async () => {
      if (controller) return;
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 5000);
      try {
        const value = await request<ApprovalInbox>('/approvals', { signal: controller.signal });
        if (!disposed) { setInbox(value); setError(undefined); }
      } catch (failure) {
        if (!disposed) setError((failure as Error).message);
      } finally { clearTimeout(timeout); controller = undefined; }
    };
    void refresh();
    const interval = setInterval(() => void refresh(), 1500);
    window.addEventListener(changed, refresh);
    return () => {
      disposed = true; clearInterval(interval); controller?.abort();
      window.removeEventListener(changed, refresh);
    };
  }, [enabled]);
  return { inbox, error };
}

export async function decideApproval(id: string, decision: ApprovalDecision, note: string) {
  const record = await request<ApprovalRecord>(`/approvals/${encodeURIComponent(id)}/decision`, {
    method: 'POST', headers: { 'X-Actor': 'user:web' }, body: JSON.stringify({ decision, note }),
  });
  window.dispatchEvent(new Event(changed));
  return record;
}
