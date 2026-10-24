import { ApiError, request } from './api';

// Acquisition records are real HTTP documents. They are separate from immutable
// imports and from per-version usage: only a succeeded result names a local asset.
export type AcquisitionStatus =
  | 'queued' | 'downloading' | 'preparing' | 'validating' | 'importing'
  | 'succeeded' | 'failed' | 'cancelled';
export type AcquisitionPhase = 'queued' | 'downloading' | 'preparing' | 'validating' | 'importing';

export interface AcquisitionProgress {
  phase: AcquisitionPhase;
  completedBytes: number;
  totalBytes: number;
  completedFiles: number;
  totalFiles: number;
  message: string | null;
  updatedAt: string;
}

export interface AcquisitionUsage {
  sourceBytes: number | null;
  transferBytes: number | null;
  cacheReusedBytes: number | null;
  adaptedBytes: number | null;
  temporaryBytes: number | null;
}

export interface AcquisitionLimits {
  sourcePackageBytes: number;
  adaptedPackageBytes: number;
  batchBytes: number;
  rawCacheBytes: number;
  freeDiskMinBytes: number;
}

export interface AcquisitionEstimate {
  source: {
    bytes: number; files: number; licenseFiles: string[]; bomItems: number;
    bomNotInBom: string[];
  };
  cache: { reusedBytes: number | null; known: boolean; reason: string };
  transfer: { upperBoundBytes: number };
  temporary: { upperBoundBytes: number };
  adaptedPackage: { bytes: number | null; upperBoundBytes: number; known: boolean };
  requiredDiskBytes: number;
  limits: AcquisitionLimits;
  withinLimits: boolean;
  license: { admitted: boolean; reasons: string[] };
  usage: { dressing: boolean | null; staticCollision: boolean | null };
  acquisition: { eligible: boolean; reasons: string[] };
  unknown: string[];
  networkRequired: boolean;
}

export interface AcquisitionBudget {
  reservationId: string;
  sourceBytes: number;
  adaptedUpperBytes: number;
  reservedDiskBytes: number;
  batchBytes: number;
  releasedAt: string | null;
}

export interface AcquisitionResult {
  assetVersion: string;
  contentHash: string;
  deduplicated: boolean;
  validation: { status: 'launchable' | 'gaps'; reportId: string | null; gapCount: number };
  usage: { dressing: boolean | null; staticCollision: boolean | null; evidence: string | null };
}

export interface AcquisitionFailure {
  code: string;
  message: string;
  retryable: boolean;
}

export interface AcquisitionRecord {
  schema: 'lw.acquisition/1';
  id: string;
  catalogRef: string;
  status: AcquisitionStatus;
  attempt: number;
  source: { provider: string; packageId: string; revision: string; recipe: string };
  estimate: AcquisitionEstimate;
  budget: AcquisitionBudget;
  progress: AcquisitionProgress;
  usage: AcquisitionUsage;
  result: AcquisitionResult | null;
  error: AcquisitionFailure | null;
  createdAt: string;
  updatedAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  cancelRequested: boolean;
}

export interface AcquisitionList {
  acquisitions: AcquisitionRecord[];
}

export const acquisitionStatuses: AcquisitionStatus[] = [
  'queued', 'downloading', 'preparing', 'validating', 'importing',
  'succeeded', 'failed', 'cancelled',
];

export const acquisitionStatusLabels: Record<AcquisitionStatus, string> = {
  queued: '排队中',
  downloading: '下载中',
  preparing: '准备中',
  validating: '校验中',
  importing: '导入中',
  succeeded: '已成功',
  failed: '失败',
  cancelled: '已取消',
};

export const acquisitionPhaseLabels: Record<AcquisitionPhase, string> = {
  queued: '排队', downloading: '下载', preparing: '准备', validating: '校验', importing: '导入',
};

export const acquisitionErrorLabels: Record<string, string> = {
  source_invalid: '来源无效',
  adaptation_gap: '适配缺口',
  import_invalid: '导入无效',
  budget_exceeded: '超出预算',
  source_unavailable: '来源不可用',
  lfs_pointer: 'LFS 指针',
  interrupted: '被重启中断',
  internal_error: '内部错误',
};

export const isActiveAcquisition = (record: AcquisitionRecord) =>
  record.status === 'queued' || record.status === 'downloading' || record.status === 'preparing'
  || record.status === 'validating' || record.status === 'importing';

export const startAcquisition = (ref: string, signal?: AbortSignal) =>
  request<AcquisitionRecord>('/library/acquisitions', {
    method: 'POST', body: JSON.stringify({ ref }), signal,
  });

export const listAcquisitions = (status?: AcquisitionStatus, signal?: AbortSignal) =>
  request<AcquisitionList>(
    `/library/acquisitions${status ? `?status=${encodeURIComponent(status)}` : ''}`, { signal },
  );

export const getAcquisition = (id: string, signal?: AbortSignal) =>
  request<AcquisitionRecord>(`/library/acquisitions/${encodeURIComponent(id)}`, { signal });

export const cancelAcquisition = (id: string) =>
  request<AcquisitionRecord>(`/library/acquisitions/${encodeURIComponent(id)}/cancel`, { method: 'POST' });

export const retryAcquisition = (id: string) =>
  request<AcquisitionRecord>(`/library/acquisitions/${encodeURIComponent(id)}/retry`, { method: 'POST' });

export interface LocalAssetTarget { kind: string; name: string; rev: string }

/** Only a published local version becomes a library link; progress is never a version. */
export function localAssetTarget(record: AcquisitionRecord): LocalAssetTarget | null {
  const version = record.result?.assetVersion;
  if (!version) return null;
  const [bare, rev] = version.split('@');
  const [kind, name] = (bare ?? '').split('/');
  if (!kind || !name || !rev) return null;
  return { kind, name, rev };
}

/** Structured 409/404 reasons stay visible; unknown gateway errors stay honest. */
export function acquisitionErrorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : String(error);
  const detail = error.detail as { code?: string; reasons?: string[] } | undefined;
  const reasons = Array.isArray(detail?.reasons) ? detail.reasons : [];
  return [error.message, ...reasons].join('；');
}
