import { createContext, useEffect, useState } from 'react';
import { request } from './api';
import type { Actor, Catalog, EntityAlert, Condition } from './protocol';

export interface AlertEpisode extends EntityAlert {
  sessionId: string; active: boolean;
  readOnly?: boolean;
  acknowledgement: { actor: Actor; note: string; simTime: number; sampleTimeMs: number } | null;
  closedContext: { reason: string; epoch: number; simTime: number } | null;
}
export interface AlertState { active?: AlertEpisode[]; recent?: AlertEpisode[]; critical?: AlertEpisode; activeCapped?: boolean; error?: string }
export const AlertContext = createContext<AlertState>({});
const changed = 'lab:alerts-changed';

export async function fetchAlerts(signal?: AbortSignal): Promise<AlertState> {
  const [active, recent, critical] = await Promise.all([
    request<{ alerts: AlertEpisode[] }>('/alerts?active=true&limit=1000', { signal }),
    request<{ alerts: AlertEpisode[] }>('/alerts?limit=100', { signal }),
    request<{ alerts: AlertEpisode[] }>('/alerts?active=true&severity=critical&limit=1', { signal }),
  ]);
  return { active: active.alerts, recent: recent.alerts, critical: critical.alerts[0], activeCapped: active.alerts.length === 1000 };
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

/** Python fnmatchcase semantics, including slash, Unicode, negated sets and invalid ranges. */
function matchesEntityPattern(value: string, pattern: string): boolean {
  const source = Array.from(pattern), characters = Array.from(value);
  const tokens: (string | RegExp | null)[] = [];
  for (let i = 0; i < source.length; i++) {
    const character = source[i];
    if (character === '*') { if (tokens.at(-1) !== null) tokens.push(null); }
    else if (character === '?') tokens.push(/^[\s\S]$/u);
    else if (character !== '[') tokens.push(character);
    else {
      let end = i + 1;
      if (source[end] === '!') end++;
      if (source[end] === ']') end++;
      while (end < source.length && source[end] !== ']') end++;
      if (end === source.length) { tokens.push('['); continue; }
      const chunks: string[][] = [];
      let start = i + 1, cursor = source[start] === '!' ? start + 2 : start + 1;
      while (true) {
        cursor = source.indexOf('-', cursor);
        if (cursor < 0 || cursor >= end) break;
        chunks.push(source.slice(start, cursor)); start = cursor + 1; cursor += 3;
      }
      const tail = source.slice(start, end);
      if (tail.length) chunks.push(tail); else chunks.at(-1)!.push('-');
      // fnmatch removes reversed ranges, including their endpoints.
      for (let index = chunks.length - 1; index > 0; index--) {
        if (chunks[index - 1].at(-1)!.codePointAt(0)! > chunks[index][0].codePointAt(0)!) {
          chunks[index - 1] = [...chunks[index - 1].slice(0, -1), ...chunks[index].slice(1)]; chunks.splice(index, 1);
        }
      }
      const negated = chunks[0]?.[0] === '!';
      if (negated) chunks[0].shift();
      const contents = chunks.map(chunk => chunk.map(character => ['\\', '-', ']', '[', '^'].includes(character) ? `\\${character}` : character).join('')).join('-');
      tokens.push(!contents ? (negated ? /^[\s\S]$/u : /(?!)/u) : new RegExp(`^[${negated ? '^' : ''}${contents}]$`, 'u'));
      i = end;
    }
  }
  // Explicit reachable positions keep '*' matching bounded by pattern × key length.
  let positions = new Set([0]);
  for (const token of tokens) {
    if (token === null) {
      const start = Math.min(...positions);
      positions = new Set(Array.from({ length: characters.length - start + 1 }, (_, offset) => start + offset));
    }
    else positions = new Set([...positions].filter(position => position < characters.length &&
      (typeof token === 'string' ? token === characters[position] : token.test(characters[position]))).map(position => position + 1));
    if (!positions.size) return false;
  }
  return positions.has(characters.length);
}

/** Resolve author declarations against the actual Catalog identities, as the host does. */
export function declaredRules(document: RuleDeclarations, catalog: Catalog, source: string): DeclaredRule[] {
  const bindings = document.effectiveBindings?.[source] ?? document.entities;
  return Object.values(catalog.entities).flatMap(entity => {
    const binding = bindings.find(binding => binding.key === entity.key || (binding.match && matchesEntityPattern(entity.key, binding.match)));
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

/** Draw only static single-property thresholds; dynamic/compound rules need history of their other inputs. */
export function alertGuides(rules: DeclaredRule[], property: string, chart: NonNullable<ReturnType<typeof sparkline>>) {
  const thresholds: { rule: string; value: number; operator: string; y: number | undefined }[] = [];
  const unplotted: string[] = [];
  const operators = { lt: '<', lte: '≤', gt: '>', gte: '≥', equals: '=' };
  const clauses = (condition: Condition): Condition[] => Array.isArray(condition.all)
    ? (condition.all as Condition[]).flatMap(clauses) : [condition];
  for (const rule of rules) {
    const conditions = clauses(rule.when);
    const affectsProperty = conditions.some(condition => (condition.entity === undefined || condition.entity === rule.key)
      && (condition.property === property || condition.deviatesFrom === property));
    if (!affectsProperty) continue;
    const condition = conditions[0];
    const operator = Object.entries(operators).find(([name]) => typeof condition[name] === 'number' && Number.isFinite(condition[name]));
    if (conditions.length !== 1 || condition.property !== property || !operator) { unplotted.push(rule.name); continue; }
    const value = condition[operator[0]] as number;
    const y = value < chart.low || value > chart.high ? undefined : chart.high === chart.low ? 28
      : Number((7 + (1 - (value - chart.low) / (chart.high - chart.low)) * 42).toFixed(3));
    thresholds.push({ rule: rule.name, value, operator: operator[1], y });
  }
  return { thresholds, unplotted };
}

export const reading = (value: unknown) => value === undefined ? '—' : typeof value === 'number'
  ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(value)
  : typeof value === 'boolean' ? (value ? '是' : '否') : typeof value === 'string' ? value : JSON.stringify(value);
export const elapsed = (milliseconds: number) => milliseconds >= 60000 ? `${reading(milliseconds / 60000)} 分钟` : `${reading(milliseconds / 1000)} 秒`;
