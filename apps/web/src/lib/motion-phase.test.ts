import { expect, it } from 'vitest';
import { ghostPositionsAt, phaseTime } from './motion-phase';

it('uses simulation-time intervals and holds or wraps at the motion boundary', () => {
  expect(phaseTime(2.25, 2, true)).toBe(.25);
  expect(phaseTime(2.25, 2, false)).toBe(2);
  const samples = [{ time: 0, positions: [[0, 0, 0]] }, { time: 2, positions: [[2, 4, 6]] }] as const;
  expect(ghostPositionsAt(samples, .5)).toEqual([[.5, 1, 1.5]]);
  expect(ghostPositionsAt(samples, 2)).toEqual([[2, 4, 6]]);
});
