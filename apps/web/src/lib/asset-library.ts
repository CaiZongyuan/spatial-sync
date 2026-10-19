import { request } from './api';

export const assetKinds = { robot: '机器人', device: '设备', labware: '实验耗材', environment: '环境' } as const;
export type AssetKind = keyof typeof assetKinds;
export type CompletenessStatus = 'present' | 'warning' | 'missing' | 'not_applicable';
export const completenessLabels: Record<string, string> = {
  collision: '碰撞体', massInertia: '质量 / 惯量', visual: 'PBR 视觉', license: '许可证',
  jointSites: '关节 / 站点命名', types: '可实现类型',
};
export const statusLabels: Record<CompletenessStatus, string> = {
  present: '有', warning: '警告', missing: '缺', not_applicable: '不适用',
};
const gapLabels: Record<string, string> = {
  ...completenessLabels, structure_validation: '结构检查未完成', physics_validation: '物理检查未完成',
  dimensions: '尺寸未提供', mass: '质量未提供', mass_estimated: '质量为估计值', provenance: '来源记录缺失',
};
export interface CompletenessItem { id: string; status: CompletenessStatus; message: string }
export interface AssetVersion {
  id: string; assetId: string; assetVersion: string; rev: number; kind: AssetKind; title: string;
  description: string; root: 'builtin' | 'user'; contentHash: string; dimensionsM: number[] | null;
  implementedTypes: string[]; gaps: string[]; gapCount: number;
  completeness: { status: string; gaps: string[]; items: CompletenessItem[] };
  validationStatus: string; thumbnailUrl: string | null; visualUrl: string | null;
}
export interface AssetDetail extends AssetVersion {
  metadata: { id: string; dimensionsM?: number[]; implements?: { type: string; programs: Record<string, string>; config: Record<string, unknown> }[] };
  validationCoverage: string; provenance: Record<string, unknown> | null; files: Record<string, string>;
}
export type AssetFilters = { q?: string; kind?: AssetKind; gaps?: boolean };
export function getAssetLibrary(filters: AssetFilters, signal?: AbortSignal) {
  const params = new URLSearchParams();
  if (filters.q) params.set('q', filters.q);
  if (filters.kind) params.set('kind', filters.kind);
  if (filters.gaps) params.set('gaps', 'true');
  return request<{ assets: AssetVersion[] }>(`/library/assets?${params}`, { signal });
}
export const getAssetDetail = (kind: string, name: string, rev: string, signal?: AbortSignal) =>
  request<AssetDetail>(`/library/assets/${encodeURIComponent(kind)}/${encodeURIComponent(name)}/${encodeURIComponent(rev)}`, { signal });
export const assetParams = (asset: AssetVersion) => ({ kind: asset.kind, name: asset.assetId.split('/')[1], rev: String(asset.rev) });
export const dimensionsLabel = (values: number[] | null | undefined) => values ? `${values.map(value => Number(value.toFixed(3))).join(' × ')} m` : '未提供尺寸';
export const gapLabel = (id: string) => gapLabels[id] ?? id;
