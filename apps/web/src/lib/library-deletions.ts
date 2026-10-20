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
export function requestLibraryDeletion(resource: 'asset' | 'scene', id: string) {
  const [name, revision] = id.split('@');
  const path = resource === 'asset' ? `assets/${name.split('/').map(encodeURIComponent).join('/')}` : `scenes/${encodeURIComponent(name)}`;
  return request<{ code: 'pending_approval'; approvalId: string }>(`/library/${path}/${encodeURIComponent(revision)}`, {
    method: 'DELETE', headers: { 'X-Actor': 'user:web' },
  }).then(value => { window.dispatchEvent(new Event('lab:approvals-changed')); return value; });
}
