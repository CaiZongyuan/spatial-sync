import { expect, it } from 'vitest';
import cases from '../../../../../tests/fixtures/conditions.json';
import { evaluateConditions } from './conditions';
import type { Condition, JsonValue, ObservationMeta } from '../protocol';

it.each(cases)('$name', (test) => {
  const values = test.values as Record<string, Record<string, JsonValue>>;
  const metadata = ('meta' in test ? test.meta : undefined) as Record<string, Record<string, ObservationMeta>> | undefined;
  const failed = evaluateConditions(
      test.conditions as Condition[],
      test.entityKey,
      (key) => values[key],
      (key) => metadata?.[key],
    );
  expect(failed.length === 0).toBe(test.expected);
  if ('expectedReason' in test) expect(failed[0]).toMatchObject({
    reason: test.expectedReason, actual: 'unknown', message: '读数已过期',
  });
});
