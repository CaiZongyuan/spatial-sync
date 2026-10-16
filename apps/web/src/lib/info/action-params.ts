import Ajv from 'ajv';
import type { ActionDefinition, JsonSchema, JsonValue } from '../protocol';

export const isJsonField = (schema: JsonSchema) =>
  Array.isArray(schema.type) ||
  schema.type === 'array' ||
  schema.type === 'object' ||
  (!schema.type && !schema.enum);
export const isTextField = (schema: JsonSchema) =>
  schema.type === 'string' && !schema.enum;

export function parseActionParams(
  action: ActionDefinition,
  input: Record<string, string>,
): Record<string, JsonValue> {
  const params: Record<string, JsonValue> = {};
  for (const [name, schema] of Object.entries(action.params)) {
    if (isTextField(schema)) {
      if (Object.hasOwn(input, name)) params[name] = input[name];
      continue;
    }
    const raw = input[name] ?? '';
    if (!raw.trim()) continue;
    try {
      params[name] =
        schema.enum || isJsonField(schema) || schema.type === 'boolean'
          ? (JSON.parse(raw) as JsonValue)
          : schema.type === 'number' || schema.type === 'integer'
            ? Number(raw)
            : raw;
    } catch {
      throw new Error(`${name}: enter valid JSON`);
    }
  }
  const validate = new Ajv({ allErrors: true, strict: false }).compile({
    type: 'object',
    properties: action.params,
    required: action.required,
    additionalProperties: false,
  });
  if (!validate(params))
    throw new Error(
      validate.errors
        ?.map(
          (error) =>
            `${error.instancePath.slice(1) || error.params.missingProperty || 'Parameters'} ${error.message}`,
        )
        .join('; '),
    );
  return params;
}
