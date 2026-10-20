import { useState, useSyncExternalStore } from 'react';
import { Icon } from './Icon';
import type { SceneManifest } from '../lib/protocol';
import type { InfoStore } from '../lib/info/store';
import { terminal } from '../lib/info/store';
import { useInvocations } from '../lib/info/hooks';
import { deviceKey } from '../lib/info/binding';

export function InvocationsPanel({
  store,
  manifest,
  selected,
  simTime,
  disabled,
  onCancel,
}: {
  store: InfoStore;
  manifest: SceneManifest;
  selected: string;
  simTime: number;
  disabled: boolean;
  onCancel: (id: string) => void;
}) {
  const [filter, setFilter] = useState('');
  const [error, setError] = useState<string>();
  const metadata = useSyncExternalStore(
    store.subscribeMetadata,
    store.getMetadata,
  );
  const key = deviceKey(manifest, metadata.catalog, selected);
  const invocations = useInvocations(
    store,
    filter === 'selected' && key ? key : '',
  );
  if (!metadata.catalog || !Object.keys(metadata.catalog.entities).length)
    return null;
  return (
    <section className={`invocations-panel${!invocations.length && !error ? ' is-empty' : ''}`} aria-label="Invocations">
      <div className="invocations-heading">
        <h2>
          关联调用 <span>{invocations.length}</span>
        </h2>
        <select
          aria-label="Filter invocations"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <option value="">全部设备</option>
          <option value="selected" disabled={!key}>
            所选设备
          </option>
        </select>
      </div>
      {error && (
        <p className="action-error" role="alert">
          {error}
        </p>
      )}
      <div className="invocations-list">
        {invocations.length ? (
          invocations.slice(0, 100).map((invocation) => (
            <details
              key={invocation.invocationId}
              className="invocation-row"
              data-testid={`invocation-${invocation.invocationId}`}
            >
              <summary>
                <strong><Icon name="chevron" />{invocation.action}</strong>
                <span title={invocation.entityKey}>
                  {invocation.entityKey.split('/').at(-1)}
                </span>
                <span className={`invocation-status ${invocation.status}`}>
                  {invocation.status}
                </span>
                <progress
                  value={
                    invocation.progress ??
                    (invocation.status === 'succeeded' ? 1 : 0)
                  }
                  max={1}
                  aria-label={`${invocation.action} progress`}
                />
                <span>
                  {invocation.actor.kind}:{invocation.actor.name}
                </span>
                <time>
                  {Math.max(
                    0,
                    (invocation.endedSimTime ?? simTime) -
                      (invocation.startedSimTime ?? invocation.createdSimTime),
                  ).toFixed(2)}{' '}
                  s
                </time>
              </summary>
              <div className="invocation-details">
                <code>{invocation.invocationId}</code>
                <p>{invocation.entityKey}</p>
                <pre>
                  {JSON.stringify(
                    {
                      params: invocation.params,
                      result: invocation.result,
                      error: invocation.error,
                    },
                    null,
                    2,
                  )}
                </pre>
                {!terminal(invocation) && (
                  <button
                    aria-label={`Cancel ${invocation.action}`}
                    disabled={disabled}
                    onClick={() => {
                      try {
                        onCancel(invocation.invocationId);
                      } catch (error) {
                        setError((error as Error).message);
                      }
                    }}
                  >
                    取消 {invocation.action}
                  </button>
                )}
              </div>
            </details>
          ))
        ) : (
          <p className="field-note">
            执行设备动作后，在这里查看进度与结果。
          </p>
        )}
      </div>
    </section>
  );
}

export function ApprovalBanner({
  store,
  disabled,
  onDecision,
}: {
  store: InfoStore;
  disabled: boolean;
  onDecision: (id: string, decision: 'approve' | 'deny') => void;
}) {
  const invocations = useInvocations(store);
  const [error, setError] = useState<string>();
  const pending = invocations.filter(
    (invocation) => invocation.status === 'pending_approval',
  );
  if (!pending.length) return null;
  return (
    <section className="approval-banner" aria-label="Pending approvals">
      {pending.map((invocation) => (
        <div key={invocation.invocationId}>
          <span>
            <strong>需要审批</strong> · {invocation.action} ·{' '}
            {invocation.entityKey.split('/').at(-1)} · {invocation.actor.name}
          </span>
          {(['approve', 'deny'] as const).map((decision) => (
            <button
              key={decision}
              aria-label={`${decision === 'approve' ? 'Approve' : 'Deny'} ${invocation.action}`}
              disabled={disabled}
              onClick={() => {
                try {
                  onDecision(invocation.invocationId, decision);
                } catch (error) {
                  setError((error as Error).message);
                }
              }}
            >
              {decision === 'approve' ? '批准' : '拒绝'} {invocation.action}
            </button>
          ))}
        </div>
      ))}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
