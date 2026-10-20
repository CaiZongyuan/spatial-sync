import { afterEach, describe, expect, it, vi } from 'vitest';
import { alertGuides, declaredRules, fetchAlerts, sparkline, type DeclaredRule, type RuleDeclarations } from './live';
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

it('finds the old active critical even when 1000 newer active warnings fill the active query', async () => {
  const critical = { alertId: 'old-critical', active: true, severity: 'critical', sampleTimeMs: 0 };
  const episodes = [critical, ...Array.from({ length: 1000 }, (_, i) => ({ alertId: `warning-${i}`, active: true, severity: 'warning', sampleTimeMs: i + 1 }))];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const query = new URL(url, 'http://local').searchParams;
    const matching = episodes.filter(episode => (!query.has('active') || episode.active === (query.get('active') === 'true'))
      && (!query.has('severity') || episode.severity === query.get('severity')));
    return new Response(JSON.stringify({ alerts: matching.sort((a, b) => b.sampleTimeMs - a.sampleTimeMs).slice(0, Number(query.get('limit'))) }));
  }));
  const result = await fetchAlerts();
  expect(result.active).toHaveLength(1000);
  expect(result.activeCapped).toBe(true);
  expect(result.active?.some(alert => alert.alertId === critical.alertId)).toBe(false);
  expect(result.recent?.some(alert => alert.alertId === critical.alertId)).toBe(false);
  expect(result.critical).toEqual(critical);
});

describe('declared alert guides', () => {
  const rule: DeclaredRule = { key: '/World/BSC', name: 'lowInflow', severity: 'critical', when: { property: 'inflowMs', lt: .4 }, forSeconds: 30, message: 'Airflow below 0.4 m/s' };
  it('places the shipped static 0.4 guide within actual 0.3–0.5 data without extending tick bounds', () => {
    const chart = sparkline([[0, .3], [60000, .5]])!;
    const guides = alertGuides([rule], 'inflowMs', chart);
    expect(guides.thresholds).toEqual([{ rule: 'lowInflow', value: .4, operator: '<', y: 28 }]);
    expect(chart).toMatchObject({ low: .3, high: .5, start: 0, end: 60000 });
    expect(guides.unplotted).toEqual([]);
  });
  it('does not invent a tick for an outside threshold or reconstruct a compound/dynamic history', () => {
    const chart = sparkline([[0, .5], [60000, .6]])!;
    expect(alertGuides([rule], 'inflowMs', chart).thresholds[0]).toEqual({ rule: 'lowInflow', value: .4, operator: '<', y: undefined });
    const dynamic: DeclaredRule = { ...rule, name: 'deviation', when: { all: [{ property: 'inflowMs', deviatesFrom: 'setpoint', by: .1 }] } };
    const compound: DeclaredRule = { ...rule, name: 'compound', when: { all: [{ property: 'inflowMs', lt: .4 }, { property: 'blowerOn', equals: true }] } };
    expect(alertGuides([dynamic, compound], 'inflowMs', chart)).toEqual({ thresholds: [], unplotted: ['deviation', 'compound'] });
    expect(alertGuides([rule], 'temperatureC', chart)).toEqual({ thresholds: [], unplotted: [] });
    expect(alertGuides([rule], 'inflowMs', sparkline([[0, .4]])!).thresholds[0].y).toBe(28);
  });
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

it.each([
  ['EnvironmentSensor*', 'EnvironmentSensor1', true],
  ['EnvironmentSensor?', 'EnvironmentSensor1', true],
  ['EnvironmentSensor[1-4]', 'EnvironmentSensor1', true],
  ['EnvironmentSensor[!2-4]', 'EnvironmentSensor1', true],
  ['EnvironmentSensor[!1-4]', 'EnvironmentSensor1', false],
  ['EnvironmentSensor[]]', 'EnvironmentSensor]', true],
  ['EnvironmentSensor[[]', 'EnvironmentSensor[', true],
  ['EnvironmentSensor[', 'EnvironmentSensor[', true],
  ['EnvironmentSensor[z-a]', 'EnvironmentSensora', false],
  ['EnvironmentSensor[!z-a]', 'EnvironmentSensora', true],
  ['EnvironmentSensor[-a]', 'EnvironmentSensor-', true],
  ['EnvironmentSensor[a-]', 'EnvironmentSensor-', true],
  ['EnvironmentSensor[^]', 'EnvironmentSensor^', true],
  ['EnvironmentSensor?', 'EnvironmentSensor😀', true],
  ['EnvironmentSensor*', 'EnvironmentSensor1/child', true],
  ['EnvironmentSensor?', 'EnvironmentSensor11', false],
  ['EnvironmentSensor1', 'environmentsensor1', false],
] as const)('resolves raw Python fnmatchcase pattern %s for %s', (pattern, name, expected) => {
  const key = `/World/${name}`;
  const rule = { when: { property: 'temperatureC', gt: 38 }, forSeconds: 60, severity: 'critical', message: 'Hot' } as const;
  const document: RuleDeclarations = { contentHash: 'legacy', effectiveBindings: {}, entities: [{ match: `/World/${pattern}`, type: 'sensor/v1' }], types: { 'sensor/v1': { alerts: { hot: rule } } } };
  const catalog = { entities: { [key]: { key, type: 'sensor/v1' } } } as unknown as Catalog;
  expect(declaredRules(document, catalog, 'synthetic')).toHaveLength(expected ? 1 : 0);
});
