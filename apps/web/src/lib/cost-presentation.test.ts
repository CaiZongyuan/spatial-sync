import { expect, it } from 'vitest';
import { costExtrema, knownSegments, ratioLabel } from './cost-presentation';
import { runtimeCount, runtimeMoney } from './runtime-records';

it('leaves gaps and unknown peaks when runtime buckets are incomplete', () => {
  expect(knownSegments([0, null, .1, undefined, .2, .3])).toEqual([
    [{ index: 0, value: 0 }], [{ index: 2, value: .1 }], [{ index: 4, value: .2 }, { index: 5, value: .3 }],
  ]);
  expect(costExtrema([0, null, .00105])).toEqual({ peak: null, last: .00105 });
  expect(costExtrema([0, .00105, 0])).toEqual({ peak: .00105, last: 0 });
  expect(costExtrema([])).toEqual({ peak: null, last: null });
});

it('preserves runtime cost precision and distinguishes explicit zero from missing metering', () => {
  expect(runtimeMoney(.3244434)).toBe('$0.3244434');
  expect(runtimeMoney(0)).toBe('$0.00');
  expect(runtimeMoney(.000000002)).toBe('$2.000e-9');
  expect(runtimeMoney(null)).toBe('未知');
  expect(runtimeCount(0)).toBe('0'); expect(runtimeCount(null)).toBe('未知');
  expect(ratioLabel(.8)).toBe('80%'); expect(ratioLabel(null)).toBe('未知');
});
