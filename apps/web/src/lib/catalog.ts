import { request } from './api';

// The unified catalog search stays device-agnostic: it only exposes catalog
// candidates, their fixed source identity and bounded acquisition evidence.
export type CatalogScope = 'local' | 'online' | 'all';
export type CatalogUsage = 'dressing' | 'staticCollision';
export type CatalogEntryView = 'summary' | 'full';

export interface CatalogSnapshot {
  id: string | null;
  provider: string | null;
  providerRevision: string | null;
  indexedAt: string | null;
  entryCount: number;
  coverage: string | null;
  offlineReadable: boolean;
  networkRequired: boolean;
  ageSeconds: number | null;
}

export interface CatalogZeroResult {
  reason: 'no_query_match' | 'hard_filters' | 'scope' | 'empty_catalog' | 'unknown_exact_id' | 'response_budget';
  messages: string[];
  excludedBy: { filter: string; count: number }[];
  suggestions: { action: 'remove_filter' | 'broaden_scope' | 'enable_approximation'; filter?: string }[];
}

export interface CatalogMatchReason { field: string; value: string; query: string }
export interface CatalogMatch {
  reasons: CatalogMatchReason[];
  satisfied: string[];
  unsatisfied: string[];
  unknown: string[];
  semanticScore?: number;
}

export interface CatalogSemanticModel {
  id: string;
  version: string;
  kind: 'deterministic-stand-in' | 'real-cpu-multilingual';
  real: boolean;
  runtime: string;
  dimension: number;
  requested?: string;
  fallbackReason?: string;
}

export interface CatalogSemantic {
  schema: 'lw.catalog-semantic/1';
  enabled: boolean;
  applied: boolean;
  reason: string;
  model: CatalogSemanticModel | null;
  index: { version: string; entryCount: number } | null;
  threshold: number | null;
  minCoverage?: number | null;
  coverageBypass?: number | null;
  hits: number;
  notice: string;
}

export interface CatalogLicense {
  name: string | null;
  admitted: boolean;
  unknown: boolean;
  obligations: string[];
}

export interface CatalogInstalledVersion {
  id: string;
  contentHash: string;
  usage?: Record<CatalogUsage, boolean | null>;
  license?: { name: string | null; admitted: boolean; unknown: boolean };
  unknown?: string[];
}

export interface CatalogInstalled {
  versions: CatalogInstalledVersion[];
  latest: string | null;
  truncated?: boolean;
}

export interface CatalogLocalAsset {
  id: string;
  title: string | null;
  kind: string | null;
  dimensionsM: number[] | null;
  implementedTypes: string[];
  gaps: string[];
  gapCount: number;
  completeness: Record<string, unknown>;
  validationStatus: string | null;
  thumbnailUrl: string | null;
  visualUrl: string | null;
}

export interface CatalogLocalResult {
  id: string;
  rev: number;
  root: 'builtin' | 'user';
  contentHash: string;
  bytes: number | null;
  asset: CatalogLocalAsset;
}

export interface CatalogPreview {
  kind: 'local' | 'remote' | 'unavailable';
  url: string | null;
  reason?: string | null;
}

export interface CatalogCandidate {
  type: 'online' | 'local';
  ref: string;
  id: string;
  kind: string;
  title: string;
  names: { en: string | null; zh: string | null };
  descriptions: { en: string | null; zh: string | null };
  dimensionsM: number[] | null;
  source: { provider: string | null; packageId: string | null; revision: string | null; bytes: number | null; files: number | null };
  license: CatalogLicense;
  usage: { dressing?: boolean | null; staticCollision?: boolean | null; evidence?: string | null };
  installed: CatalogInstalled;
  acquisition: { eligible: boolean; reasons: string[] };
  match: CatalogMatch;
  unknown: string[];
  detail: { resource: string; id: string; tool: string };
  preview: CatalogPreview;
  local?: CatalogLocalResult;
}

export interface CatalogSearchResponse {
  schema: 'lw.catalog-search/1';
  scope: CatalogScope;
  interpretedQuery: {
    text: string;
    keywords: string[];
    preferences: { term: string; language: string; applied: boolean }[];
    exact: { type: string; id: string; ref?: string } | null;
    approximateRequested: boolean;
    notes: string[];
  };
  appliedFilters: Record<string, string | number | boolean>;
  unknownPolicy: 'excluded';
  snapshot: CatalogSnapshot;
  semantic: CatalogSemantic;
  counts: {
    returned: number;
    matching: number;
    localMatching: number;
    onlineMatching: number;
    approximate: number;
    budgetTruncated: boolean;
  };
  results: CatalogCandidate[];
  nextCursor: string | null;
  truncated: boolean;
  zeroResult: CatalogZeroResult | null;
}

export interface CatalogUsageFact { eligible: boolean | null; unknown: boolean; evidence: string }
export interface CatalogAttachment { file: string; license: string; evidence: string }
export interface CatalogEntryDocument {
  schema: 'lw.catalog-entry/1';
  view: 'summary' | 'full';
  documentDigest?: string;
  id: string;
  ref: string;
  provider: string;
  packageId: string;
  revision: string;
  localAssetId: string;
  kind: string;
  title: string;
  names: { en: string; zh: string };
  aliases: Record<string, string[]>;
  descriptions: Record<string, string>;
  tags: Record<string, string[]>;
  source: {
    url: string; files: number; bytes: number; rootUsd: string;
    bomItems: number; bomNotInBom: string[];
  };
  license: {
    name: string; admitted: boolean; unknown: boolean; obligations: string[];
    inBom: boolean;
    evidence: { file: string; sha256: string };
    attachments: CatalogAttachment[];
  };
  usage: Record<CatalogUsage, CatalogUsageFact>;
  adaptation: {
    status: string; gaps: string[]; dimensionsM: number[] | null;
    outputBytes: number | null; outputFiles: number | null;
    variant: string; approximations: string[];
  };
  upstreamValidation: {
    profile: string; profileVersion: string; date: string;
    failingFeatures: string[]; declaredBy?: string;
  };
  acquisition: { eligible: boolean; reasons: string[] };
  installed: CatalogInstalled;
  unknown: string[];
  evidence: Record<string, unknown>;
  preview: CatalogPreview;
  previewTruncated?: boolean;
  estimate: { resource: string; id: string; tool: string; path: string };
}

export interface CatalogEstimateDocument {
  schema: 'lw.catalog-estimate/1';
  ref: string;
  source: { bytes: number; files: number; licenseFiles: string[]; bomItems: number; bomNotInBom: string[] };
  cache: { reusedBytes: number | null; known: boolean; reason: string };
  transfer: { upperBoundBytes: number };
  temporary: { upperBoundBytes: number };
  adaptedPackage: { bytes: number | null; upperBoundBytes: number; known: boolean };
  requiredDiskBytes: number;
  limits: {
    sourcePackageBytes: number; adaptedPackageBytes: number; batchBytes: number;
    rawCacheBytes: number; freeDiskMinBytes: number;
  };
  withinLimits: boolean;
  license: { admitted: boolean; reasons: string[] };
  usage: Record<CatalogUsage, boolean | null>;
  acquisition: { eligible: boolean; reasons: string[] };
  unknown: string[];
  networkRequired: boolean;
  downloadsMainFiles: false;
  estimated: true;
}

export interface CatalogSearchFilters {
  q?: string;
  scope: CatalogScope;
  kind?: string;
  usage?: CatalogUsage;
  approximate?: boolean;
  limit?: number;
  cursor?: string;
}

/** Query parameters for the bounded unified search. Empty values are omitted. */
export function catalogSearchParams(filters: CatalogSearchFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) params.set('q', filters.q);
  params.set('scope', filters.scope);
  if (filters.kind) params.set('kind', filters.kind);
  if (filters.usage) params.set('usage', filters.usage);
  if (filters.approximate) params.set('approximate', 'true');
  if (filters.limit !== undefined) params.set('limit', String(filters.limit));
  if (filters.cursor) params.set('cursor', filters.cursor);
  return params;
}

export const catalogSearchPath = (filters: CatalogSearchFilters) =>
  `/library/search?${catalogSearchParams(filters)}`;

export const searchCatalog = (filters: CatalogSearchFilters, signal?: AbortSignal) =>
  request<CatalogSearchResponse>(catalogSearchPath(filters), { signal });

const entryBase = (provider: string, packageId: string, revision: string) =>
  `/library/catalog/entries/${encodeURIComponent(provider)}/${encodeURIComponent(packageId)}/${encodeURIComponent(revision)}`;

export const catalogEntryPath = (
  provider: string, packageId: string, revision: string, view: CatalogEntryView = 'summary',
) => `${entryBase(provider, packageId, revision)}?view=${view}`;

export const catalogEstimatePath = (provider: string, packageId: string, revision: string) =>
  `${entryBase(provider, packageId, revision)}/estimate`;

export const getCatalogEntry = (
  provider: string, packageId: string, revision: string, view: CatalogEntryView = 'full',
  signal?: AbortSignal,
) => request<CatalogEntryDocument>(catalogEntryPath(provider, packageId, revision, view), { signal });

export const getCatalogEstimate = (
  provider: string, packageId: string, revision: string, signal?: AbortSignal,
) => request<CatalogEstimateDocument>(catalogEstimatePath(provider, packageId, revision), { signal });

export const usageLabels: Record<CatalogUsage, string> = {
  dressing: '布景',
  staticCollision: '静态碰撞',
};

export const filterLabels: Record<string, string> = {
  kind: '种类', provider: '来源', usage: '用途', licenseAdmitted: '许可准入',
  installed: '本地已安装', minSourceBytes: '最小来源字节', maxSourceBytes: '最大来源字节',
  minDimensionM: '最小尺寸', maxDimensionM: '最大尺寸',
};

export const zeroReasonLabels: Record<CatalogZeroResult['reason'], string> = {
  no_query_match: '查询无匹配',
  hard_filters: '明确条件排除了候选',
  scope: '结果位于其他范围',
  empty_catalog: '目录快照为空',
  unknown_exact_id: '未知的精确 ID',
  response_budget: '响应大小超出上限',
};

export const usageLabel = (value: boolean | null | undefined) =>
  value === true ? '可用' : value === false ? '不可用' : '未知';

export const usageState = (value: boolean | null | undefined) =>
  value === true ? 'eligible' : value === false ? 'ineligible' : 'unknown';

const BYTE_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB'] as const;

/** Deterministic byte label; missing values stay explicitly unknown. */
export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '未知';
  if (value < 1024) return `${value} B`;
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < BYTE_UNITS.length - 1) { size /= 1024; unit += 1; }
  return `${size >= 100 ? size.toFixed(0) : size.toFixed(1)} ${BYTE_UNITS[unit]}`;
}

/** Coarse freshness label for a catalog snapshot age. */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '新鲜度未知';
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

export const shortRevision = (revision: string | null | undefined) =>
  revision ? `${revision.slice(0, 12)}…` : '未知';

/** Visible model label; a stand-in is never presented as a real model. */
export function semanticLabel(semantic: CatalogSemantic | undefined): string {
  if (!semantic || !semantic.enabled || !semantic.model) return '语义检索未启用';
  const model = semantic.model;
  const name = `${model.id}@${model.version}`;
  return model.real
    ? `语义检索 ${name} · 索引 ${shortRevision(semantic.index?.version)}`
    : `语义检索 CI 替身（非真实模型）· ${name} · 索引 ${shortRevision(semantic.index?.version)}`;
}
