import { expect, it } from 'vitest';
import { costExtrema, knownSegments, ratioLabel } from './cost-presentation';
import { agentMoney } from './agent-runs';

it('retains actual zero and separates known runs across unknown buckets', () => {
  expect(knownSegments([0, null, .1, undefined, .2, .3])).toEqual([
    [{ index: 0, value: 0 }], [{ index: 2, value: .1 }], [{ index: 4, value: .2 }, { index: 5, value: .3 }],
  ]);
  expect(costExtrema([0, null, .00105])).toEqual({ peak: null, last: .00105 });
  expect(costExtrema([0, .00105, 0])).toEqual({ peak: .00105, last: 0 });
  expect(costExtrema([])).toEqual({ peak: null, last: null });
});

it('displays small positive estimates, actual zero and unavailable usage distinctly', () => {
  expect(agentMoney(.00105)).toBe('$0.00105');
  expect(agentMoney(0)).toBe('$0.00');
  expect(agentMoney(.0000002)).toBe('$2.00e-7');
  expect(agentMoney(null)).toBe('未知');
  expect(ratioLabel(.8)).toBe('80%');
  expect(ratioLabel(null)).toBe('未知');
});
