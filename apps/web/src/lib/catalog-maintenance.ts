import { request } from './api';

export interface MetadataCollection {
  id: string; dataset: string; enabled: boolean; publisher?: string; revision?: string;
  coverage?: Record<string, number | boolean | null>; scheduleSeconds?: number | null;
}
export interface MetadataRun {
  id: string; collectionId: string; status: string; phase: string;
  processed?: number; failed?: number; pending?: number; message?: string;
  counts?: Record<string, number | null>; usage?: Record<string, number | null>;
  coverage?: { discovered: number; indexed: number; organized: number; failed: number; reused: number;
    extracted?: number; extractionPending?: number; extractionFailed?: number };
  errors?: { message: string; entry?: string }[];
  organizer?: 'runtime' | 'external' | 'none';
  previewCoverage?: { available: number; missing: number; failed: number; pending: number; reused: number };
}
export interface MetadataState {
  collections: MetadataCollection[]; runs: MetadataRun[];
  runtime: { configured: boolean; profile?: string | null }; skillsVersion?: string;
  capabilities?: { maintenance?: boolean; externalOrganization: boolean; localThumbnails: boolean; nativeQueries: boolean };
  release?: { id: string; collections: { id: string; coverage: Record<string, number | boolean | null> }[] } | null;
}
export interface CatalogVocabulary {
  version: string;
  categories: { id: string; name: { en: string; zh: string } }[];
  tags: { id: string; name: { en: string; zh: string } }[];
}
const base = '/library/catalog/maintenance';
// A host that states it has no maintenance runs must not offer run controls;
// older hosts omit the flag and keep the full panel.
export const maintenanceRunsAvailable = (state?: MetadataState) =>
  state?.capabilities?.maintenance !== false;
export const getMetadataState = (signal?: AbortSignal) => request<MetadataState>(base, { signal });
export const getCatalogVocabulary = (version?: string, signal?: AbortSignal) =>
  request<CatalogVocabulary>(`${base}/vocabulary${version ? `?version=${encodeURIComponent(version)}` : ''}`, { signal });
export const startMetadataRun = (collectionId: string, organizer = 'runtime', previews = false) => request<MetadataRun>(`${base}/runs`, {
  method: 'POST', body: JSON.stringify({ collectionId, organizer, previews }),
});
export const controlMetadataRun = (id: string, action: 'cancel' | 'retry' | 'resume') =>
  request<MetadataRun>(`${base}/runs/${encodeURIComponent(id)}/${action}`, { method: 'POST' });
export const setMetadataCollectionEnabled = (id: string, enabled: boolean) =>
  request<MetadataCollection>(`${base}/collections/${encodeURIComponent(id)}`, {
    method: 'PATCH', body: JSON.stringify({ enabled }),
  });
export const evidencePath = (digest: string) => `${base}/evidence/${encodeURIComponent(digest)}`;
