import { describe, expect, it } from 'vitest';
import { lineage, liveCells, materialState, quantity, rows } from './model';
import type { EntityValues } from '../protocol';
const entity = (snapshot: unknown) => ({ key: '/workcell', rev: 1, values: { materialSnapshot: snapshot }, meta: {} }) as EntityValues;
describe('authoritative passage material consumers', () => {
  it('does not treat missing, partial, or unreconciled material as safe', () => {
    expect(materialState(undefined).reconciled).toBe(false);
    expect(materialState(entity({ revision: 1, reconciled: true })).known).toBe(false);
    expect(materialState(entity({ revision: 1, vessels: [], reservations: [], observations: [], operations: [], reconciled: false })).reconciled).toBe(false);
    expect(quantity(undefined)).toBe('未知');
  });
  it('links configured culture identities, not carried origin cells or vessel names', () => {
    const vessels = rows([{ id: 'mother/well', culture: 'origin', parent: '', cells: [] },
      { id: 'daughter-a', culture: 'child-a', parent: 'origin', cells: [] },
      { id: 'daughter-b', culture: 'child-b', parent: 'origin', cells: [] },
      { id: 'tube', culture: '', parent: 'origin', cells: [{ culture: 'origin' }] }]);
    expect(lineage(vessels)[0].children.map(row => row.id)).toEqual(['daughter-a', 'daughter-b']);
  });
  it('includes attached, suspended and pellet cells and preserves unknown quantities', () => {
    expect(liveCells({ cells: [{ attachedLive: 20, suspendedLive: 30, pelletLive: 50 }] })).toBe(100);
    expect(liveCells({ cells: [{ attachedLive: 20 }] })).toBeUndefined();
  });
});
