import { expect, it } from 'vitest';
import cases from '../../../../../tests/fixtures/conditions.json';
import { evaluateConditions } from './conditions';
import type { Condition, JsonValue } from '../protocol';

it.each(cases)('$name', (test) => {
  const values = test.values as Record<string, Record<string, JsonValue>>;
  expect(
    evaluateConditions(
      test.conditions as Condition[],
      test.entityKey,
      (key) => values[key],
    ).length === 0,
  ).toBe(test.expected);
});
