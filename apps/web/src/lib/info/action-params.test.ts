import { expect, it } from 'vitest';
import type { ActionDefinition } from '../protocol';
import { parseActionParams } from './action-params';

it('submits typed schema fields, omits optional blanks and rejects invalid limits and JSON arrays', () => {
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
      note: '',
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
