import { useEffect, useState } from 'react';
import type { SceneManifest } from '../lib/protocol';
import type { SessionClient, SubscriptionOptions } from '../lib/stream/client';

export function SubscriptionControls({
  client,
  manifest,
  selected,
}: {
  client: SessionClient;
  manifest: SceneManifest;
  selected: string;
}) {
  const [scope, setScope] = useState<SubscriptionOptions['scope']>('all');
  const [delta, setDelta] = useState(true);
  const groups = manifest.nodes.filter((node) => node.kind === 'group');
  const [deviceKey, setDeviceKey] = useState(groups[0]?.entityKey ?? '');
  const selectedEntity = scope === 'entities' ? selected : '';
  const selectedDevice = scope === 'device' ? deviceKey : '';
  useEffect(() => {
    if (scope === 'entities' && !selectedEntity) return;
    if (scope === 'device' && !selectedDevice) return;
    client.setSubscription({
      scope,
      delta,
      ...(scope === 'entities' ? { entityKeys: [selectedEntity] } : {}),
      ...(scope === 'device' ? { deviceKey: selectedDevice } : {}),
    });
  }, [client, scope, delta, selectedEntity, selectedDevice]);
  return (
    <div className="subscription-controls control-bar">
      <label>
        Stream scope{' '}
        <select
          aria-label="Stream scope"
          value={scope}
          onChange={(event) =>
            setScope(event.target.value as SubscriptionOptions['scope'])
          }
        >
          <option value="all">All bodies</option>
          <option value="entities" disabled={!selected}>
            Selected entity
          </option>
          <option value="device" disabled={!groups.length}>
            Device / group
          </option>
          <option value="viewport">Camera viewport</option>
        </select>
      </label>
      {scope === 'device' && (
        <select
          aria-label="Subscription group"
          value={deviceKey}
          onChange={(event) => setDeviceKey(event.target.value)}
        >
          {groups.map((node) => (
            <option key={node.entityKey} value={node.entityKey}>
              {node.name}
            </option>
          ))}
        </select>
      )}
      <label>
        <input
          type="checkbox"
          aria-label="Sparse deltas"
          checked={delta}
          onChange={(event) => setDelta(event.target.checked)}
        />{' '}
        Sparse deltas
      </label>
      <span className="muted">
        {client.buffer.memberIds.length} subscribed bodies
      </span>
    </div>
  );
}
