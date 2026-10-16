import type { Condition, JsonValue } from '../protocol';

const operators = [
  'equals',
  'notEquals',
  'in',
  'notIn',
  'lt',
  'lte',
  'gt',
  'gte',
  'isNull',
  'notNull',
] as const;
export type ConditionFailure = {
  condition: Condition;
  actual: JsonValue | 'unknown';
  message: string;
};

function equal(a: JsonValue, b: JsonValue): boolean {
  if (a === b) return true;
  if (
    a === null ||
    b === null ||
    typeof a !== 'object' ||
    typeof b !== 'object'
  )
    return false;
  if (Array.isArray(a) || Array.isArray(b))
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((value, index) => equal(value, b[index]))
    );
  return (
    Object.keys(a).length === Object.keys(b).length &&
    Object.keys(a).every(
      (key) => Object.hasOwn(b, key) && equal(a[key], b[key]),
    )
  );
}

export function evaluateConditions(
  conditions: Condition[],
  entityKey: string,
  read: (key: string) => Record<string, JsonValue> | undefined,
): ConditionFailure[] {
  return conditions.flatMap((condition) => {
    const property = String(condition.property);
    const values = read(
      typeof condition.entity === 'string' ? condition.entity : entityKey,
    );
    const known = values !== undefined && Object.hasOwn(values, property);
    const actual = known ? values[property] : undefined;
    const op = operators.find((operator) => Object.hasOwn(condition, operator));
    const expected = op ? condition[op] : undefined;
    let passed = false;
    if (known && op) {
      if (op === 'equals' || op === 'notEquals') {
        const same = equal(actual!, expected!);
        passed = op === 'equals' ? same : !same;
      } else if ((op === 'in' || op === 'notIn') && Array.isArray(expected)) {
        const found = expected.some((value) => equal(actual!, value));
        passed = op === 'in' ? found : !found;
      } else if (op === 'isNull') passed = actual === null;
      else if (op === 'notNull') passed = actual !== null;
      else if (typeof actual === 'number' && typeof expected === 'number') {
        if (op === 'lt') passed = actual < expected;
        if (op === 'lte') passed = actual <= expected;
        if (op === 'gt') passed = actual > expected;
        if (op === 'gte') passed = actual >= expected;
      }
    }
    return passed
      ? []
      : [
          {
            condition,
            actual: known ? actual! : 'unknown',
            message:
              typeof condition.message === 'string'
                ? condition.message
                : `Condition on ${property} is not satisfied`,
          },
        ];
  });
}
