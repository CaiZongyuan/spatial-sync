import { createContext, useEffect, useState } from 'react';
import { request } from './api';
import { listNodes, type TrainingNode } from './jobs';
import type { LlmModel } from './model-resources';

export const costRanges = ['1h', '1d', '7d', '30d', 'custom'] as const;
export type CostRange = typeof costRanges[number];
export type CostDimension = 'agent' | 'model' | 'node' | 'goal' | 'plan' | 'job';
export interface CostFilters { range: CostRange; agent?: string; model?: string; node?: string; goal?: string; plan?: string; start?: string; end?: string }
export interface CostTotals {
  requests: number | null; failures: number | null; tokens: number | null;
  inputTokens: number | null; outputTokens: number | null; cacheReadTokens: number | null;
  llmUsd: number | null; successRate: number | null; cacheHitRate: number | null;
  gpuSeconds: number; gpuHours: number; reportedGpuSeconds: number; estimatedGpuSeconds: number;
  historicalGpuSeconds: number; memoryPeakMb: number | null;
  gpuUsd: number | null; knownGpuUsd: number; unpricedGpuSeconds: number;
  priceRevisions: number[]; priceWarnings: string[];
}
export interface CostWindow { key: string; start: number; end: number; bucketSeconds: number; timezone: 'UTC' }
export interface CostSummary {
  window: CostWindow; totals: CostTotals; cards: Record<'today' | '7d' | 'all', CostTotals>;
  choices: Record<CostDimension, string[]>; availability: { llm: boolean; reason: string }; pricingPolicy: string;
}
export interface CostBucket extends CostTotals { start: number; end: number }
export interface CostBreakdown {
  window: CostWindow; by: CostDimension;
  rows: (CostTotals & { key: string | null; label: string; gpuShare: number | null;
    jobs: (CostTotals & { jobId: string; nodeId: string | null; status: string })[] })[];
}
export interface CostAnomaly {
  id: string; kind: 'job' | 'evaluation'; severity: 'error' | 'warning'; title: string; detail: string;
  at: number | null; read: boolean; jobId?: string; plan?: string; evaluationId?: string;
}
export interface CostAnomalies { anomalies: CostAnomaly[]; unreadCount: number; canAcknowledge: boolean }
export interface CostAnomalyState { data?: CostAnomalies; error?: string }
export const CostAnomalyContext = createContext<CostAnomalyState>({});
const changed = 'lab:cost-anomalies-changed';

export function costFilters(input: Record<string, unknown>): CostFilters {
  const result: CostFilters = { range: costRanges.includes(input.range as CostRange) ? input.range as CostRange : '7d' };
  for (const key of ['agent', 'model', 'node', 'goal', 'plan', 'start', 'end'] as const)
    if (typeof input[key] === 'string' && input[key].length > 0 && input[key].length <= 4000) result[key] = input[key];
  return result;
}
export function costQuery(filters: CostFilters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value != null && value !== '') params.set(key, value);
  return params.toString();
}
export function useCostAnomalies(enabled = true): CostAnomalyState {
  const [state, setState] = useState<CostAnomalyState>({});
  useEffect(() => {
    if (!enabled) return;
    let disposed = false, pending: AbortController | undefined;
    const refresh = async () => {
      if (pending) return;
      const controller = new AbortController(); pending = controller;
      const timeout = setTimeout(() => controller.abort(), 5000);
      try { const data = await request<CostAnomalies>('/cost/anomalies', { signal: controller.signal }); if (!disposed) setState({ data }); }
      catch (error) { if (!disposed) setState({ error: (error as Error).message }); }
      finally { clearTimeout(timeout); pending = undefined; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 3000);
    window.addEventListener(changed, refresh);
    return () => { disposed = true; clearInterval(timer); pending?.abort(); window.removeEventListener(changed, refresh); };
  }, [enabled]);
  return enabled ? state : {};
}
export async function acknowledgeCostAnomaly(id: string) {
  await request(`/cost/anomalies/${encodeURIComponent(id)}/read`, { method: 'POST', headers: { 'X-Actor': 'user:web' } });
  window.dispatchEvent(new Event(changed));
}

export function useCostDashboard(filters: CostFilters) {
  const key = costQuery(filters);
  const [response, setResponse] = useState<{ key: string; summary: CostSummary; buckets: CostBucket[];
    goals: CostBreakdown; plans: CostBreakdown; nodes: CostBreakdown; connectedNodes: TrainingNode[]; models: LlmModel[] }>();
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let disposed = false, pending: AbortController | undefined;
    const refresh = async () => {
      if (pending) return;
      const controller = new AbortController(); pending = controller;
      const timeout = setTimeout(() => controller.abort(), 10000);
      const options = { signal: controller.signal };
      try {
        const [summary, series, goals, plans, nodes, connectedNodes, models] = await Promise.all([
          request<CostSummary>(`/cost/summary?${key}`, options), request<{ buckets: CostBucket[] }>(`/cost/series?${key}`, options),
          request<CostBreakdown>(`/cost/breakdown?${key}&by=goal`, options), request<CostBreakdown>(`/cost/breakdown?${key}&by=plan`, options),
          request<CostBreakdown>(`/cost/breakdown?${key}&by=node`, options), listNodes(controller.signal),
          request<{ models: LlmModel[] }>('/models', options),
        ]);
        if (!disposed) { setResponse({ key, summary, buckets: series.buckets, goals, plans, nodes, connectedNodes, models: models.models }); setFailure(undefined); }
      } catch (error) { if (!disposed) setFailure({ key, message: (error as Error).message }); }
      finally { clearTimeout(timeout); pending = undefined; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 5000);
    return () => { disposed = true; clearInterval(timer); pending?.abort(); };
  }, [key, retry]);
  return { data: response?.key === key ? response : undefined, error: failure?.key === key ? failure.message : undefined,
    refresh: () => setRetry(value => value + 1) };
}
