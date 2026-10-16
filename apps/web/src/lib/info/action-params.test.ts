import { expect, it } from 'vitest';
import type { ActionDefinition } from '../protocol';
import { parseActionParams } from './action-params';

it('submits typed schema fields, omits untouched optional fields and rejects invalid limits and JSON arrays', () => {
  const action = {
    params: {
      rpm: { type: 'number', minimum: 500, maximum: 15000 },
      enabled: { type: 'boolean' },
      positions: {
        type: 'array',
        items: { type: 'number' },
        minItems: 2,
        maxItems: 2,
      },
      mode: { enum: ['fast', 'slow'] },
      note: { type: 'string' },
    },
    required: ['rpm', 'positions'],
  } as unknown as ActionDefinition;
  expect(
    parseActionParams(action, {
      rpm: '3000',
      positions: '[1,2]',
      enabled: 'false',
      mode: '"fast"',
    }),
  ).toEqual({ rpm: 3000, positions: [1, 2], enabled: false, mode: 'fast' });
  expect(() =>
    parseActionParams(action, { rpm: '1', positions: '[1,2]' }),
  ).toThrow(/rpm/);
  expect(() =>
    parseActionParams(action, { rpm: '3000', positions: '[1]' }),
  ).toThrow(/positions/);
  expect(() =>
    parseActionParams(action, { rpm: '3000', positions: 'nope' }),
  ).toThrow(/positions/);
});

it('preserves explicitly supplied empty and whitespace strings using JSON Schema character counts', () => {
  const action = {
    params: {
      label: { type: 'string' },
      note: { type: 'string' },
      symbol: { type: 'string', maxLength: 1 },
    },
    required: ['label'],
  } as unknown as ActionDefinition;
  expect(
    parseActionParams(action, { label: '', note: '  ', symbol: '😀' }),
  ).toEqual({ label: '', note: '  ', symbol: '😀' });
  expect(parseActionParams(action, { label: ' ' })).toEqual({ label: ' ' });
  expect(parseActionParams(action, { label: '', note: '' })).toEqual({
    label: '',
    note: '',
  });
  expect(() => parseActionParams(action, { label: '', symbol: 'AB' })).toThrow(
    /symbol/,
  );
});
