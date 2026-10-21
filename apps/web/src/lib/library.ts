import { request, type SessionLaunch } from './api';
import { readRecording, type Recording } from './replay/reader';

export type SceneSource = 'synthetic' | 'newton';
export interface ScenePreview {
  status: 'available' | 'unavailable';
  source: SceneSource;
  reason: string | null;
  entityCount: number | null;
  bindingCount: number | null;
  groupCount?: number;
  sceneVersion?: string;
  sceneContentHash?: string;
  capturedAt?: string;
  simTime?: number;
  runtime?: Record<string, string>;
  bounds?: { min: number[]; max: number[]; scope: string } | null;
  documents?: { manifest: string; catalog: string; spatial: string; state: string };
  thumbnail?: Record<string, string>;
}
export interface SceneVersion {
  id: string;
  sceneId: string;
  sceneVersion: string;
  rev: number;
  title: string;
  description: string;
  sources: SceneSource[];
  root: 'builtin' | 'user';
  contentHash: string;
  launchable: boolean;
  launchReason: string | null;
  stage: string;
  validationStatus: string;
  entityCount: number | null;
  deviceCount: number | null;
  countsReason: string | null;
  preview: ScenePreview;
}
export interface ValidationReport {
  subject: string;
  ranAt?: string;
  launchable: boolean;
  physics?: {
    status: 'completed' | 'skipped'; reason?: string; ranAt?: string;
    retainedFrom?: { reportId?: string; ranAt?: string };
  };
  checks: { id: string; tier: string; status: 'pass' | 'warn' | 'fail'; message: string;
    entityKey?: string; location?: string }[];
}
export interface SceneDetail extends SceneVersion {
  metadata: { name: string; title: string; stage: string; description?: string;
    simulation?: { solver?: string; warmupSteps?: number } };
  entities: { key?: string; match?: string; type: string; program: string }[];
  locations: Record<string, { site?: string; zone?: string; p?: number[]; approach?: number[]; grasp?: number[] }>;
  validation: ValidationReport | null;
  inspection: string;
  recordings: Recording[];
}

export const getSceneLibrary = (filters: { q?: string; launchable?: boolean; source?: SceneSource }, signal?: AbortSignal) => {
  const params = new URLSearchParams();
  if (filters.q) params.set('q', filters.q);
  if (filters.launchable !== undefined) params.set('launchable', String(filters.launchable));
  if (filters.source) params.set('source', filters.source);
  return request<{ scenes: SceneVersion[] }>(`/library/scenes?${params}`, { signal });
};
export const getSceneDetail = async (name: string, rev: string, source?: SceneSource, signal?: AbortSignal) => {
  const value = await request<SceneDetail>(`/library/scenes/${encodeURIComponent(name)}/${encodeURIComponent(rev)}${source ? `?source=${source}` : ''}`, { signal });
  return { ...value, recordings: value.recordings.map(readRecording) };
};
export const launchScene = (sceneVersion: string, source: SceneSource) => request<SessionLaunch>('/sessions', {
  method: 'POST', body: JSON.stringify({ sceneVersion, source }),
});
export const sourceLabel = (source: SceneSource) => source === 'synthetic' ? 'Synthetic · 无需 GPU' : 'Newton';
export const rootLabel = (root: SceneVersion['root']) => root === 'builtin' ? '内置库' : '用户库';
