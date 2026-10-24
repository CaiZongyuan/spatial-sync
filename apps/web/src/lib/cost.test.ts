import { describe, expect, it } from 'vitest';
import { costFilters, costQuery } from './cost';

describe('shareable cost filters', () => {
  it('round trips exact Plan revisions, Agent and model references, dates and custom range', () => {
    const filters = costFilters({ range: 'custom', agent: 'lab-operator', model: 'provider/org/model@1',
      plan: 'plan-123@2', start: '2026-10-01T00:00:00+08:00', end: '2026-10-08T00:00:00+08:00' });
    expect(costFilters(Object.fromEntries(new URLSearchParams(costQuery(filters))))).toEqual(filters);
  });
  it('uses the documented range default and drops empty or non-string filters', () => {
    expect(costFilters({ range: 'bogus', agent: '', model: false, plan: ['plan-1'] })).toEqual({ range: '7d' });
    expect(costQuery({ range: '1h', node: undefined, goal: '' })).toBe('range=1h');
  });
});
