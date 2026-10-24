import { request } from './api';

export interface AuthoringObjectBinding {
  kind: 'scene' | 'asset';
  id: string;
  contentHash: string;
}
export interface AuthoringArtifact { name: string; reference: string }
export interface AuthoringReportPreview {
  schema: string;
  submissionId: string;
  source: string;
  sourceVersion: string;
  reportedAt: string;
  receivedAt: string;
  draftId: string | null;
  object: AuthoringObjectBinding | null;
  summaryPreview: string;
  summaryBytes: number;
  summaryTruncated: boolean;
  gapCount: number;
  gapsPreview: string[];
  artifactCount: number;
}
export interface AuthoringReport {
  schema: string;
  submissionId: string;
  source: string;
  sourceVersion: string;
  reportedAt: string;
  receivedAt: string;
  draftId: string | null;
  object: AuthoringObjectBinding | null;
  summary: string;
  summaryBytes: number;
  gaps: string[];
  artifacts: AuthoringArtifact[];
}

export const getAuthoringReports = (filters: { scene?: string; draft?: string; limit?: number }, signal?: AbortSignal) => {
  const params = new URLSearchParams();
  if (filters.scene) params.set('scene', filters.scene);
  if (filters.draft) params.set('draft', filters.draft);
  if (filters.limit !== undefined) params.set('limit', String(filters.limit));
  return request<{ reports: AuthoringReportPreview[] }>(`/library/authoring-reports?${params}`, { signal });
};
export const getAuthoringReport = (submissionId: string, signal?: AbortSignal) =>
  request<AuthoringReport>(`/library/authoring-reports/${encodeURIComponent(submissionId)}`, { signal });
