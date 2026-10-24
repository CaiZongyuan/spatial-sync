import { request } from './api';

export type RuntimeFormat = 'claude-code-stream-json' | 'claude-code-session-jsonl';
export interface RuntimeAssociations {
  agentId?: string | null; harnessVersion?: number | null; planId?: string | null;
  planRevision?: number | null; goalId?: string | null;
}
export interface RuntimeUsage {
  source: 'runtime-import'; scope: 'capture' | 'messages' | 'unknown';
  basis: 'runtime-result' | 'runtime-messages';
  inputTokens: number | null; outputTokens: number | null; cacheReadTokens: number | null;
  cacheWriteTokens: number | null; tokens: number | null; requests: number | null; turns: number | null;
  llmUsd: number | null; costSource: 'runtime-reported' | 'runtime-estimate' | 'unknown';
  coverage: { tokens: 'reported' | 'partial' | 'unknown'; cost: 'reported' | 'unknown'; requests: 'reported' | 'unknown' };
}
export interface RuntimeArtifact {
  id: 'transcript' | 'usage'; kind: 'runtime-transcript' | 'runtime-usage'; source: 'runtime-import';
  url: string; size: number; sha256: string;
}
export interface RuntimeSessionSummary {
  id: string; runtime: 'claude-code' | 'pi'; format: string; nativeSessionId: string; captureId: string;
  runtimeVersion: string | null; source: 'runtime-import'; sourceLabel: string | null; contentHash: string;
  createdAt: string | null; updatedAt: string | null; importedAt: string; lastImportedAt: string;
  status: 'observed' | 'completed' | 'error'; eventCount: number; messageCount: number; toolCallCount: number;
  models: string[]; thinking: string | null; associations: RuntimeAssociations; usage: RuntimeUsage;
  artifacts: RuntimeArtifact[];
}
export interface RuntimeEvent {
  id: string; nativeId: string | null; kind: 'message' | 'tool' | 'metadata' | 'result'; nativeType: string;
  role: string | null; timestamp: string | null; content: Record<string, unknown>[] | string | null;
  model: string | null; parentId: string | null; contentHash: string;
}
export interface RuntimeSessionDetail extends RuntimeSessionSummary { events: RuntimeEvent[] }
export interface RuntimeImportResult {
  session: RuntimeSessionSummary; addedEvents: number; updatedEvents: number; idempotent: boolean;
}
export interface RuntimeImportInput {
  format: RuntimeFormat; content: string; sourceLabel?: string; associations?: RuntimeAssociations;
}
export const runtimeChanged = 'lab:runtime-records-changed';
export const importRuntimeCapture = async (input: RuntimeImportInput, signal?: AbortSignal) => {
  const result = await request<RuntimeImportResult>('/runtime-sessions/import', {
    method: 'POST', body: JSON.stringify(input), signal,
  });
  window.dispatchEvent(new Event(runtimeChanged));
  return result;
};
export const listRuntimeSessions = async (q = '', signal?: AbortSignal) =>
  (await request<{ sessions: RuntimeSessionSummary[] }>(`/runtime-sessions${q ? `?${new URLSearchParams({ q })}` : ''}`, { signal })).sessions;
export const readRuntimeSession = (id: string, signal?: AbortSignal) =>
  request<RuntimeSessionDetail>(`/runtime-sessions/${encodeURIComponent(id)}`, { signal });
export const runtimeCount = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '未知' :
  value.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
export const runtimeMoney = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? '未知' :
  value !== 0 && Math.abs(value) < .00000001 ? `$${value.toExponential(3)}` :
    `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 8 })}`;
export const runtimeTime = (value: string | null | undefined) => value == null ? '未提供' :
  new Date(value).toLocaleString('zh-CN', { hour12: false });
export const runtimeStatus = { observed: '已观测记录', completed: '运行时已结束', error: '运行时错误' };
export const runtimeCostSource = { 'runtime-reported': '运行时报告', 'runtime-estimate': '运行时估算（原样）', unknown: '运行时未提供费用' };
