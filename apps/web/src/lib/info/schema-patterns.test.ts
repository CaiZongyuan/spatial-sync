import Ajv from 'ajv';
import { expect, it } from 'vitest';
import cases from '../../../../../tests/fixtures/schema-patterns.json';

// Python separately proves these exact public schemas are its accepted exports.
// Ajv's default Unicode mode exercises the browser/MCP JSON Schema consumer.
it.each(cases.filter((item) => item.accepted))('$name', (item) => {
  const validate = new Ajv({ allErrors: true }).compile(item.schema);
  for (const value of item.valid ?? [])
    expect(validate(value), JSON.stringify(value)).toBe(true);
  for (const value of item.invalid ?? [])
    expect(validate(value), JSON.stringify(value)).toBe(false);
});
