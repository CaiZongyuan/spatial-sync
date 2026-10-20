import { createContext, useEffect, useState } from 'react';
import { request } from './api';
import type { Actor, Catalog, EntityAlert, Condition } from './protocol';

export interface AlertEpisode extends EntityAlert {
  sessionId: string; active: boolean;
  acknowledgement: { actor: Actor; note: string; simTime: number; sampleTimeMs: number } | null;
  closedContext: { reason: string; epoch: number; simTime: number } | null;
}
export interface AlertState { active?: AlertEpisode[]; recent?: AlertEpisode[]; activeCapped?: boolean; error?: string }
export const AlertContext = createContext<AlertState>({});
const changed = 'lab:alerts-changed';

export async function fetchAlerts(signal?: AbortSignal): Promise<AlertState> {
  const [active, recent] = await Promise.all([
    request<{ alerts: AlertEpisode[] }>('/alerts?active=true&limit=1000', { signal }),
    request<{ alerts: AlertEpisode[] }>('/alerts?limit=100', { signal }),
  ]);
  return { active: active.alerts, recent: recent.alerts, activeCapped: active.alerts.length === 1000 };
}

export function useAlerts(enabled: boolean): AlertState {
  const [state, setState] = useState<AlertState>({});
  useEffect(() => {
    if (!enabled) { setState({}); return; }
    let disposed = false;
    let pending: AbortController | undefined;
    const refresh = async () => {
      if (pending) return;
      const controller = new AbortController(); pending = controller;
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const value = await fetchAlerts(controller.signal);
        if (!disposed) setState(value);
      } catch (error) { if (!disposed) setState({ error: (error as Error).message }); }
      finally { clearTimeout(timeout); pending = undefined; }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 1500);
    window.addEventListener(changed, refresh);
    return () => { disposed = true; clearInterval(timer); pending?.abort(); window.removeEventListener(changed, refresh); };
  }, [enabled]);
  return enabled ? state : {};
}

export async function acknowledgeAlert(id: string, note: string) {
  const value = await request<AlertEpisode>(`/alerts/${encodeURIComponent(id)}/ack`, {
    method: 'POST', headers: { 'X-Actor': 'user:web' }, body: JSON.stringify({ note }),
  });
  window.dispatchEvent(new Event(changed));
  return value;
}

export interface AlertRule { when: Condition; forSeconds: number; severity: 'info' | 'warning' | 'critical'; message: string; hint?: string }
export interface DeclaredRule extends AlertRule { key: string; name: string }
interface RuleBinding { key?: string; match?: string; type: string; alerts?: Record<string, AlertRule> }
export interface RuleDeclarations {
  contentHash: string; entities: RuleBinding[]; effectiveBindings?: Record<string, RuleBinding[]>;
  types: Record<string, { alerts?: Record<string, AlertRule> }>;
}

/** Resolve author declarations against the actual Catalog identities, as the host does. */
export function declaredRules(document: RuleDeclarations, catalog: Catalog, source: string): DeclaredRule[] {
  const bindings = document.effectiveBindings?.[source] ?? document.entities;
  return Object.values(catalog.entities).flatMap(entity => {
    const binding = bindings.find(binding => binding.key === entity.key || (binding.match &&
      new RegExp(`^${binding.match.split('*').map(part => part.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*')}$`).test(entity.key)));
    if (!binding || binding.type !== entity.type) return [];
    return Object.entries({ ...document.types[entity.type]?.alerts, ...binding.alerts })
      .map(([name, rule]) => ({ ...rule, key: entity.key, name }));
  });
}

export type HistoryPoint = [number, unknown];
export function sparkline(points: HistoryPoint[]) {
  const samples = points.filter((point): point is [number, number] =>
    Number.isFinite(point[0]) && typeof point[1] === 'number' && Number.isFinite(point[1]));
  if (!samples.length) return undefined;
  const start = Math.min(...samples.map(point => point[0])), end = Math.max(...samples.map(point => point[0]));
  const low = Math.min(...samples.map(point => point[1])), high = Math.max(...samples.map(point => point[1]));
  const x = (at: number) => 6 + (end === start ? .5 : (at - start) / (end - start)) * 224;
  const y = (value: number) => 7 + (high === low ? .5 : 1 - (value - low) / (high - low)) * 42;
  const path = samples.map(([at, value], i) => `${i ? 'L' : 'M'}${x(at).toFixed(2)},${y(value).toFixed(2)}`).join(' ');
  const last = samples.at(-1)!;
  return { path, start, end, low, high, last: [x(last[0]), y(last[1])] as const, count: samples.length };
}

export const reading = (value: unknown) => value === undefined ? '—' : typeof value === 'number'
  ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(value)
  : typeof value === 'boolean' ? (value ? '是' : '否') : typeof value === 'string' ? value : JSON.stringify(value);
export const elapsed = (milliseconds: number) => milliseconds >= 60000 ? `${reading(milliseconds / 60000)} 分钟` : `${reading(milliseconds / 1000)} 秒`;
