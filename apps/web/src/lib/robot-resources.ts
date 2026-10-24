import { useEffect, useState } from 'react';
import { request } from './api';

export interface RobotModel {
  id: string; title: string; family: 'vla' | 'rl-policy' | 'scripted'; source: string; license: string | null;
  parameters: number | null; embodiments: string[]; upstreamEmbodiments: string[]; actionSpace: string;
  compute: { inference: string }; runnable: boolean;
  capability: { program: string | null; sources: string[]; requirements: string[]; notes: string };
  quality: { status: 'not-evaluated' | 'evaluated'; evaluationId: string | null };
  sources: { title: string; url: string; retrievedOn: string }[]; contentHash: string; root: string;
}
export interface ControllerVersion {
  controller: string; version: number; embodiment: string; robotModel: string; policy: string | null;
  program: string; action: { space: string; hz: number | null }; observation: string | null;
  task: string | null; scene: string; evaluationId: string | null; contentHash: string;
}
export interface RobotEmbodiment {
  id: string; title: string; kind: 'sim' | 'real'; asset: string; controller: string | null;
  bindings: { scene: string; entity: string; source: string; sceneContentHash: string; program: string;
    robotModel: string | null; graspMode: string | null }[];
  visualUrl: string | null; assetContentHash: string; connection: 'not-connected' | 'simulation';
  currentController: ControllerVersion | null; controllerHistory: ControllerVersion[];
}
export function useRobotResources() {
  const [data, setData] = useState<{ models: RobotModel[]; embodiments: RobotEmbodiment[] }>();
  const [error, setError] = useState<string>(), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let disposed = false;
    const controller = new AbortController(), options = { signal: controller.signal };
    const timeout = setTimeout(() => controller.abort(), 8000);
    void Promise.all([
      request<{ models: RobotModel[] }>('/robot-models', options),
      request<{ embodiments: RobotEmbodiment[] }>('/embodiments', options),
    ]).then(([models, robots]) => {
      if (!disposed) { setData({ models: models.models, embodiments: robots.embodiments }); setError(undefined); }
    }).catch(error => {
      if (!disposed) { setError(controller.signal.aborted ? '读取机器人资源超时，请重试。' : (error as Error).message); controller.abort(); }
    }).finally(() => clearTimeout(timeout));
    return () => { disposed = true; clearTimeout(timeout); controller.abort(); };
  }, [attempt]);
  return { data, error, refresh: () => setAttempt(value => value + 1) };
}
