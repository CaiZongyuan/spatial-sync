import { request } from './api';
import type { SessionState } from './protocol';

export interface NewtonViewer {
  status: 'not_started' | 'ready' | 'unavailable';
  url: string | null;
  clientCount: number | null;
  reason: string | null;
}

export interface PlatformSession {
  evaluationId?: string;
  sessionId: string;
  sceneId: string;
  sceneVersion?: string;
  sceneName: string;
  source: 'synthetic' | 'newton';
  state: SessionState['state'];
  connectedClients: number;
  simTime: number | null;
  actualRtf: number | null;
  backend: {
    pid: number | null;
    alive: boolean;
    device: string | null;
    cpuPercent: number | null;
    memoryBytes: number | null;
    metricsReason: string | null;
    viewer: NewtonViewer;
  } | null;
}

export interface PlatformSnapshot {
  sampledAtMs: number;
  samplingIntervalSeconds: number;
  summary: {
    totalSessions: number;
    runningSessions: number;
    pausedSessions: number;
    connectedSessions: number;
    connectedClients: number;
    newtonBackends: number;
  };
  sessions: PlatformSession[];
  system: {
    cpu: { percent: number | null; logicalCores: number | null; reason: string | null };
    memory: { totalBytes: number | null; usedBytes: number | null; availableBytes: number | null; percent: number | null; reason: string | null };
    gpus: { index: number; name: string; utilizationPercent: number | null; memoryUsedBytes: number | null; memoryTotalBytes: number | null; temperatureC: number | null }[];
    gpuStatus: 'available' | 'unavailable' | 'unsupported';
    gpuMessage: string | null;
  };
}

export const getPlatform = (signal: AbortSignal) => request<PlatformSnapshot>('/platform', { signal });
export const openNewtonViewer = (sessionId: string, signal: AbortSignal) => request<{ status: 'ready'; url: string; backendPid: number }>(
  `/sessions/${encodeURIComponent(sessionId)}/viewer`, { method: 'POST', signal },
);

export const percent = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? '未知' : `${value.toFixed(1)}%`;

export function bytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '未知';
  if (value < 1024) return `${value.toFixed(0)} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
}
