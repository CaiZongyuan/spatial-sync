import { describe, expect, it } from 'vitest';
import { remainingSeconds } from './training-presentation';

describe('remaining job execution time', () => {
  it.each(['succeeded', 'failed', 'cancelled'] as const)('stops forecasting when a running job becomes %s', status => {
    expect(remainingSeconds('running', 10, 5)).toBe(5);
    expect(remainingSeconds(status, 10, 5)).toBe(0);
    expect(remainingSeconds(status, null, undefined)).toBe(0);
  });

  it('keeps unavailable running estimates unknown and clamps an overrun to zero', () => {
    expect(remainingSeconds('running', null, 5)).toBeNull();
    expect(remainingSeconds('running', 10, undefined)).toBeNull();
    expect(remainingSeconds('queued', null, undefined)).toBeNull();
    expect(remainingSeconds('running', 10, 12)).toBe(0);
  });
});
