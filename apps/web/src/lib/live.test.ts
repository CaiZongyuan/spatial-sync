import { afterEach, describe, expect, it, vi } from 'vitest';
import { declaredRules, fetchAlerts, sparkline, type RuleDeclarations } from './live';
import type { Catalog } from './protocol';

afterEach(() => vi.unstubAllGlobals());

it('queries active alerts separately so 1001 newer closed episodes cannot hide an old active critical', async () => {
  const oldActive = { alertId: 'still-active', active: true, severity: 'critical', sampleTimeMs: 0 };
  const episodes = [oldActive, ...Array.from({ length: 1001 }, (_, i) => ({ alertId: `closed-${i}`, active: false, severity: 'critical', sampleTimeMs: i + 1 }))];
  const fetch = vi.fn(async (url: string) => {
    const query = new URL(url, 'http://local').searchParams;
    const matching = episodes.filter(episode => !query.has('active') || episode.active === (query.get('active') === 'true'));
    return new Response(JSON.stringify({ alerts: matching.sort((a, b) => b.sampleTimeMs - a.sampleTimeMs).slice(0, Number(query.get('limit'))) }));
  });
  vi.stubGlobal('fetch', fetch);
  const result = await fetchAlerts();
  expect(result.active).toEqual([oldActive]);
  expect(result.recent).toHaveLength(100);
  expect(result.recent?.some(episode => episode.alertId === 'still-active')).toBe(false);
  expect(result.activeCapped).toBe(false);
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ alerts: Array.from({ length: 1000 }, () => oldActive) }))));
  expect((await fetchAlerts()).activeCapped).toBe(true);
});

describe('history chart factual ticks', () => {
  it('marks only the actual reached data range, including a short new recording', () => {
    const chart = sparkline([[120000, 37.1], [180000, 38.6]])!;
    expect(chart).toMatchObject({ start: 120000, end: 180000, low: 37.1, high: 38.6, count: 2 });
    expect(chart.end - chart.start).toBe(60000);
    expect(chart.path).toBe('M6.00,49.00 L230.00,7.00');
  });
  it('keeps a single point at zero elapsed time and constant values finite', () => {
    expect(sparkline([[1000, 37]])).toMatchObject({ start: 1000, end: 1000, low: 37, high: 37, path: 'M118.00,28.00' });
    expect(sparkline([[1000, 37], [2000, 37]])?.path).toBe('M6.00,28.00 L230.00,28.00');
    expect(sparkline([[0, null], [100, false], [200, NaN]])).toBeUndefined();
  });
});

it('merges type defaults with pinned source binding rule replacements and actual identities', () => {
  const normal = { when: { property: 'temperatureC', gt: 38 }, forSeconds: 60, severity: 'critical', message: 'Hot' } as const;
  const document: RuleDeclarations = { contentHash: 'pinned', entities: [], types: { 'incubator/v1': { alerts: { hot: normal, door: { ...normal, severity: 'warning' } } } },
    effectiveBindings: { synthetic: [{ key: '/World/Incubator', type: 'incubator/v1', alerts: { hot: { ...normal, forSeconds: .25 } } }], newton: [] } };
  const catalog = { entities: { '/World/Incubator': { key: '/World/Incubator', type: 'incubator/v1' } } } as unknown as Catalog;
  expect(declaredRules(document, catalog, 'synthetic').map(rule => [rule.name, rule.forSeconds])).toEqual([['hot', .25], ['door', 60]]);
  expect(declaredRules(document, catalog, 'newton')).toEqual([]);
});
