import type { Condition, JsonValue, ObservationMeta } from '../protocol';

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
  'stale',
  'deviatesFrom',
] as const;
export type ConditionFailure = {
  condition: Condition;
  actual: JsonValue | 'unknown';
  message: string;
  reason?: 'stale_reading';
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
  readMeta?: (key: string) => Record<string, ObservationMeta> | undefined,
): ConditionFailure[] {
  return conditions.flatMap((condition) => {
    if (Array.isArray(condition.all))
      return evaluateConditions(condition.all as Condition[], entityKey, read, readMeta);
    const property = String(condition.property);
    const target = typeof condition.entity === 'string' ? condition.entity : entityKey;
    const values = read(target);
    const op = operators.find((operator) => Object.hasOwn(condition, operator));
    const reference = op === 'deviatesFrom' ? String(condition.deviatesFrom) : undefined;
    const referenceValue = reference ? values?.[reference] : undefined;
    const expired = readMeta?.(target)?.[property]?.stale === true ||
      (reference !== undefined && readMeta?.(target)?.[reference]?.stale === true);
    const known = values !== undefined && Object.hasOwn(values, property);
    const actual = known ? values[property] : undefined;
    const expected = op ? condition[op] : undefined;
    let passed = false;
    if (known && op) {
      if (op === 'stale') passed = expired === expected;
      else if (expired) passed = false;
      else if (op === 'deviatesFrom') {
        passed = typeof actual === 'number' && Number.isFinite(actual) &&
          typeof referenceValue === 'number' && Number.isFinite(referenceValue) &&
          typeof condition.by === 'number' && Number.isFinite(condition.by) && condition.by >= 0 &&
          Math.abs(actual - referenceValue) > condition.by;
      }
      else if (op === 'equals' || op === 'notEquals') {
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
            actual: known && !(expired && op !== 'stale') &&
              !(op === 'deviatesFrom' && (typeof actual !== 'number' || typeof referenceValue !== 'number'))
                ? actual! : 'unknown',
            message:
              expired && op !== 'stale' ? '读数已过期' : typeof condition.message === 'string'
                ? condition.message
                : `Condition on ${property} is not satisfied`,
            ...(expired && op !== 'stale' ? { reason: 'stale_reading' as const } : {}),
          },
        ];
  });
}
