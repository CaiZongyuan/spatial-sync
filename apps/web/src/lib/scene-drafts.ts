import { request } from './api';

export type Position = [number, number, number];
export type Orientation = [number, number, number, number];

export interface AssetPlacement {
  name: string;
  asset: string;
  contentHash: string;
  position: Position;
  orientation: Orientation;
}

export interface DraftWaypoint {
  p: Position;
  yaw: number;
}

export interface SceneDraft {
  schema: 'lw.scene-draft/1';
  name: string;
  title: string;
  assets: AssetPlacement[];
  objects?: { name: string }[];
  waypoints: Record<string, DraftWaypoint>;
}

interface SceneDraftReceipt { id: string; draft: SceneDraft }

export const getSceneDraft = (id: string, signal: AbortSignal) =>
  request<SceneDraftReceipt>(`/library/scene-drafts/${encodeURIComponent(id)}`, { signal });

export const saveSceneDraft = (draft: SceneDraft) =>
  request<SceneDraftReceipt>('/library/scene-drafts', { method: 'POST', body: JSON.stringify(draft) });
