import { expect, it } from 'vitest';
import { bytes, percent } from './platform';

it('distinguishes unavailable observations from a measured zero, including process CPU above one core', () => {
  expect(percent(null)).toBe('未知');
  expect(bytes(undefined)).toBe('未知');
  expect(percent(0)).toBe('0.0%');
  expect(bytes(0)).toBe('0 B');
  expect(percent(240.5)).toBe('240.5%');
  expect(bytes(3 * 1024 ** 3)).toBe('3.0 GiB');
});
