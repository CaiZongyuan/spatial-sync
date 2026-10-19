import { useEffect, useState } from 'react';
import type {
  ActionDefinition,
  Condition,
  EntityBinding,
  EntityType,
  JsonSchema,
  JsonValue,
} from '../lib/protocol';
import type { InfoStore } from '../lib/info/store';
import { useEntity, useInvocations } from '../lib/info/hooks';
import { evaluateConditions } from '../lib/info/conditions';
import {
  isJsonField,
  isTextField,
  parseActionParams,
} from '../lib/info/action-params';

const display = (value: unknown) =>
  typeof value === 'string' ? value : JSON.stringify(value);

function ConditionStatus({
  condition,
  entityKey,
  store,
}: {
  condition: Condition;
  entityKey: string;
  store: InfoStore;
}) {
  const target =
    typeof condition.entity === 'string' ? condition.entity : entityKey;
  const state = useEntity(store, target);
  const failure = evaluateConditions([condition], entityKey, (key) =>
    key === target ? state?.values : undefined,
    (key) => key === target ? state?.meta : undefined,
  )[0];
  return (
    <li className={failure ? 'condition unmet' : 'condition met'}>
      <span aria-label={failure ? 'Not satisfied' : 'Satisfied'}>
        {failure ? '○' : '✓'}
      </span>
      <span>
        {typeof condition.message === 'string'
          ? condition.message
          : `${String(condition.property)} ${Object.entries(condition)
              .filter(
                ([key]) => !['property', 'entity', 'message'].includes(key),
              )
              .map(([op, value]) => `${op} ${display(value)}`)
              .join(' ')}`}
        {failure && <small>{failure.reason === 'stale_reading' ? '读数已过期' : `Current: ${display(failure.actual)}`}</small>}
      </span>
    </li>
  );
}

function Parameter({
  name,
  schema,
  required,
  value,
  change,
  action,
  disabled,
}: {
  name: string;
  schema: JsonSchema;
  required: boolean;
  value: string;
  change: (value: string) => void;
  action: string;
  disabled: boolean;
}) {
  const label = `${action} ${name}`;
  const common = {
    disabled,
    value,
    onChange: (
      event: React.ChangeEvent<
        HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
      >,
    ) => change(event.target.value),
    'aria-label': label,
    required: required && !isTextField(schema),
  };
  return (
    <label className="action-field">
      <span>
        {name}
        {required ? ' *' : ''}
        {typeof schema.unit === 'string' && <small>{schema.unit}</small>}
      </span>
      {Array.isArray(schema.enum) ? (
        <select {...common}>
          <option value="">{required ? 'Choose…' : 'Not supplied'}</option>
          {schema.enum.map((value, index) => (
            <option key={index} value={JSON.stringify(value)}>
              {display(value)}
            </option>
          ))}
        </select>
      ) : schema.type === 'boolean' ? (
        <select {...common}>
          <option value="">{required ? 'Choose…' : 'Not supplied'}</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : isJsonField(schema) ? (
        <textarea
          {...common}
          rows={2}
          placeholder={schema.type === 'array' ? '[…]' : 'JSON value'}
        />
      ) : (
        <input
          {...common}
          type={
            schema.type === 'number' || schema.type === 'integer'
              ? 'number'
              : 'text'
          }
          step={schema.type === 'integer' ? 1 : 'any'}
          min={typeof schema.minimum === 'number' ? schema.minimum : undefined}
          max={typeof schema.maximum === 'number' ? schema.maximum : undefined}
        />
      )}
    </label>
  );
}

function ActionForm({
  name,
  action,
  binding,
  store,
  disabled,
  onAction,
}: {
  name: string;
  action: ActionDefinition;
  binding: EntityBinding;
  store: InfoStore;
  disabled: boolean;
  onAction: (
    key: string,
    action: string,
    params: Record<string, JsonValue>,
  ) => string;
}) {
  const [input, setInput] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(action.params)
        .filter(
          ([name, schema]) =>
            action.required.includes(name) && isTextField(schema),
        )
        .map(([name]) => [name, '']),
    ),
  );
  const [error, setError] = useState<string>();
  const [requestId, setRequestId] = useState<string>();
  const [waiting, setWaiting] = useState(false);
  const invocations = useInvocations(store, binding.key);
  const outcome = invocations.find(
    (invocation) => invocation.requestId === requestId,
  );
  useEffect(() => {
    if (outcome) setWaiting(false);
  }, [outcome]);
  useEffect(() => {
    if (!waiting) return;
    const timer = setTimeout(() => {
      setWaiting(false);
      setError(
        'Still waiting for the action outcome. Check recent invocations before retrying.',
      );
    }, 8000);
    return () => clearTimeout(timer);
  }, [waiting]);
  const conditions = [
    ...action.preconditions,
    ...(binding.interlocks[name] ?? []),
  ];
  return (
    <details className="action-card" data-testid={`action-${name}`}>
      <summary>
        <strong>{name}</strong>
        <span>
          {action.risk === 'high'
            ? 'Approval may be required'
            : action.longRunning
              ? 'Long running'
              : 'Action'}
        </span>
      </summary>
      <form
        aria-label={`Action ${name}`}
        onSubmit={(event) => {
          event.preventDefault();
          if (disabled || waiting) return;
          setError(undefined);
          try {
            setRequestId(
              onAction(binding.key, name, parseActionParams(action, input)),
            );
            setWaiting(true);
          } catch (error) {
            setError((error as Error).message);
          }
        }}
      >
        <p className="action-description">{action.description}</p>
        {conditions.length > 0 && (
          <ul className="conditions" aria-label={`${name} preconditions`}>
            {conditions.map((condition, index) => (
              <ConditionStatus
                key={index}
                {...{ condition, store }}
                entityKey={binding.key}
              />
            ))}
          </ul>
        )}
        {Object.entries(action.params).map(([param, schema]) => (
          <Parameter
            key={param}
            name={param}
            action={name}
            schema={schema}
            disabled={disabled || waiting}
            required={action.required.includes(param)}
            value={input[param] ?? ''}
            change={(value) =>
              setInput((previous) => ({ ...previous, [param]: value }))
            }
          />
        ))}
        <button
          className="primary"
          disabled={disabled || waiting}
          type="submit"
        >
          {waiting ? 'Requesting…' : `Run ${name}`}
        </button>
        {error && (
          <p role="alert" className="action-error">
            {error}
          </p>
        )}
        {outcome && (
          <p className={`action-outcome ${outcome.status}`} role="status">
            {outcome.status}
            {outcome.error
              ? ` · ${outcome.error.code}: ${outcome.error.message}`
              : ''}
          </p>
        )}
      </form>
    </details>
  );
}

export function EntityPanel({
  store,
  binding,
  definition,
  disabled,
  disabledReason,
  onAction,
  compact = false,
}: {
  store: InfoStore;
  binding: EntityBinding;
  definition: EntityType;
  disabled: boolean;
  disabledReason?: string;
  compact?: boolean;
  onAction: (
    key: string,
    action: string,
    params: Record<string, JsonValue>,
  ) => string;
}) {
  const state = useEntity(store, binding.key);
  const invocations = useInvocations(store, binding.key);
  return (
    <div className="inspector-content entity-panel" data-testid="entity-panel">
      <span className="eyebrow">DEVICE · {definition.type}</span>
      <h3>{definition.title}</h3>
      <code className="entity-key">{binding.key}</code>
      <p className="field-note">{definition.description}</p>
      {!compact && <section className="inspector-section">
        <div className="section-label">
          PROPERTIES <span>rev {state?.rev ?? '—'}</span>
        </div>
        <dl className="device-properties">
          {Object.entries(definition.properties).map(([name, property]) => {
            const meta = state?.meta[name];
            return (
              <div key={name} data-testid={`property-${name}`} className={meta?.stale ? 'stale-reading' : undefined}>
                <dt title={property.description}>{name}</dt>
                <dd>
                  <output>
                    {state && Object.hasOwn(state.values, name)
                      ? display(state.values[name])
                      : 'Unknown'}
                  </output>
                  {meta?.stale && <span className="reading-status">过期</span>}
                  {property.unit && (
                    <span className="unit">{property.unit}</span>
                  )}
                  <small
                    title={
                      meta ? `sampleTimeMs ${meta.sampleTimeMs}` : undefined
                    }
                  >
                    {meta
                      ? `simTime ${meta.simTime.toFixed(3)} s · ${meta.source}`
                      : 'Awaiting observation'}
                  </small>
                </dd>
              </div>
            );
          })}
        </dl>
      </section>}
      <section className="inspector-section">
        <div className="section-label">
          ACTIONS <span>{Object.keys(definition.actions).length}</span>
        </div>
        {disabled && (
          <p className="field-note">
            {disabledReason ?? 'Actions require a connected operator and synchronized device state.'}
          </p>
        )}
        {Object.entries(definition.actions).map(([name, action]) => (
          <ActionForm
            key={`${binding.key}:${name}`}
            {...{ name, action, binding, store, disabled, onAction }}
          />
        ))}
      </section>
      {!compact && <section className="inspector-section">
        <div className="section-label">RECENT INVOCATIONS</div>
        {invocations.length ? (
          invocations.slice(0, 5).map((invocation) => (
            <div key={invocation.invocationId} className="recent-invocation">
              <strong>{invocation.action}</strong>
              <span className={`invocation-status ${invocation.status}`}>
                {invocation.status}
              </span>
            </div>
          ))
        ) : (
          <p className="field-note">No invocations yet.</p>
        )}
      </section>}
    </div>
  );
}
