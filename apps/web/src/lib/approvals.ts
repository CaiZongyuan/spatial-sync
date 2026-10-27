import { createContext, useEffect, useState } from 'react';
import { request } from './api';
import type { DeletionDetails } from './library-deletions';
import type { TrainingPlanChange } from './training';
import type { LoopBudget } from './notebooks';

export type ApprovalActor = { kind: 'user' | 'cli' | 'agent'; name: string };
export type ApprovalDecision = 'approve' | 'deny' | 'revoke';
export interface ApprovalRecord {
  approvalId: string;
  subject: 'invocation' | 'plan' | 'policy' | 'job' | 'deletion';
  subjectId: string;
  sessionId: string | null;
  title: string;
  details: { controllerPromotionId?: string; agentPromotionId?: string; agentId?: string; entityKey?: string; params?: Record<string, unknown>; plan?: Record<string, unknown>; revision?: number; changes?: TrainingPlanChange[]; estimatedGpuHours?: number; remainingGpuHours?: number; planId?: string; planRevision?: number; loopBudget?: LoopBudget } & Partial<DeletionDetails>;
  requestedBy: ApprovalActor;
  createdAtMs: number;
  status: 'pending' | 'approved' | 'denied' | 'revoked' | 'expired' | 'unavailable';
  decisions: { decision: ApprovalDecision; actor: ApprovalActor; atMs: number; note: string }[];
  canRevoke: boolean;
  invocation: { status: string; startedSimTime: number | null } | null;
}
export interface ApprovalInbox {
  approvals: ApprovalRecord[]; pendingCount: number; role: 'operator' | 'viewer';
  pagination?: { total: number; nextCursor: string | null }; recentApprovals?: ApprovalRecord[];
}
export interface ApprovalInboxState { inbox?: ApprovalInbox; error?: string }
export const ApprovalInboxContext = createContext<ApprovalInboxState>({});

const changed = 'lab:approvals-changed';

export interface ApprovalFilters { status?: string; subject?: string; layer?: string; agentId?: string; limit?: number; cursor?: string; include?: string }
export function useApprovalInbox(enabled = true, filters?: ApprovalFilters) {
  const key = JSON.stringify(filters ?? null);
  const selectionKey = JSON.stringify(filters ? { ...filters, include: undefined } : null);
  const [response, setResponse] = useState<{ key: string; inbox?: ApprovalInbox; error?: string }>();
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let controller: AbortController | undefined;
    const refresh = async () => {
      if (controller) return;
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 5000);
      try {
        const query = new URLSearchParams({ view: filters ? 'page' : 'summary' });
        for (const [name, value] of Object.entries(filters ?? {})) if (value !== undefined) query.set(name, String(value));
        const value = await request<ApprovalInbox>(`/approvals?${query}`, { signal: controller.signal });
        if (!disposed) setResponse({ key: selectionKey, inbox: value });
      } catch (failure) {
        if (!disposed) setResponse(prior => ({ key: selectionKey, inbox: prior?.key === selectionKey ? prior.inbox : undefined, error: (failure as Error).message }));
      } finally { clearTimeout(timeout); controller = undefined; }
    };
    void refresh();
    const interval = setInterval(() => void refresh(), 1500);
    window.addEventListener(changed, refresh);
    return () => {
      disposed = true; clearInterval(interval); controller?.abort();
      window.removeEventListener(changed, refresh);
    };
  }, [enabled, key, selectionKey]);
  return response?.key === selectionKey ? { inbox: response.inbox, error: response.error } : {};
}

export async function decideApproval(id: string, decision: ApprovalDecision, note: string) {
  const record = await request<ApprovalRecord>(`/approvals/${encodeURIComponent(id)}/decision`, {
    method: 'POST', headers: { 'X-Actor': 'user:web' }, body: JSON.stringify({ decision, note }),
  });
  window.dispatchEvent(new Event(changed));
  return record;
}
