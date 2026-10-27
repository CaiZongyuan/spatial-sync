import { request } from './api';

export interface LibraryReference { kind: string; id: string; reason: string }
export interface ReferenceInspection {
  references: LibraryReference[];
  complete: boolean;
  uninspected: LibraryReference[];
  coverage: string[];
  training: string;
}
export interface DeletionExecution {
  status: 'not_started' | 'blocked' | 'failed' | 'in_progress' | 'committed';
  message?: string;
  cleanupPending?: boolean;
  error?: Partial<ReferenceInspection> & { code?: string; message?: string };
}
export interface DeletionDetails {
  resource: 'asset' | 'scene'; id: string; root: 'user'; contentHash: string;
  inspection: ReferenceInspection; execution: DeletionExecution;
}
export interface LibraryRemoval {
  schema: 'lw.library-deletion/1'; id: string; resource: 'asset' | 'scene'; versionId: string;
  contentHash: string; requestId: string; createdAtMs: number; retainedBytes: number;
  status: 'queued' | 'checking' | 'committed' | 'blocked' | 'failed' | 'restoring' | 'restored';
  error?: (Partial<ReferenceInspection> & { code: string; message: string }) | null;
}
const removedPath = (id: string) => `/library/deletions/${encodeURIComponent(id)}`;
export const removalPending = (value: LibraryRemoval) => ['queued', 'checking', 'restoring'].includes(value.status);
export const requestLibraryDeletion = (resource: 'asset' | 'scene', id: string, contentHash: string, requestId: string) =>
  request<LibraryRemoval>('/library/deletions', { method: 'POST', headers: { 'X-Actor': 'user:web' },
    body: JSON.stringify({ resource, id, contentHash, requestId }) });
export const getLibraryRemoval = (id: string, signal?: AbortSignal) =>
  request<LibraryRemoval>(removedPath(id), { signal });
export const restoreLibraryVersion = (id: string) => request<LibraryRemoval>(`${removedPath(id)}/restore`, {
  method: 'POST', headers: { 'X-Actor': 'user:web' },
});
export const listLibraryRemovals = (signal?: AbortSignal, cursor?: string) =>
  request<{ deletions: LibraryRemoval[]; pagination: { total: number; nextCursor: string | null } }>(
    `/library/deletions?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal });
