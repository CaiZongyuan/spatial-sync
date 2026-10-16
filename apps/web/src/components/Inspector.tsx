import { useEffect, useState, useSyncExternalStore } from 'react';
import { deviceKey } from '../lib/info/binding';
import { EntityPanel } from './EntityPanel';
import type { SceneManifest, Pose } from '../lib/protocol';
import type { SessionClient, ViewState } from '../lib/stream/client';
import { multiplyPose } from '../lib/scene/pose';
import { jointPositionError } from '../lib/scene/joint-error';
import { request } from '../lib/api';

const format = (value: number) => value.toFixed(6);

export function Inspector({
  manifest,
  selected,
  client,
  state,
}: {
  manifest: SceneManifest;
  selected?: string;
  client: SessionClient;
  state: ViewState;
}) {
  const [jointState, setJointState] = useState<unknown>();
  const [tab, setTab] = useState<'spatial' | 'device'>('spatial');
  const metadata = useSyncExternalStore(
    client.info.subscribeMetadata,
    client.info.getMetadata,
  );
  const key = deviceKey(manifest, metadata.catalog, selected);
  const device = key ? metadata.catalog?.entities[key] : undefined;
  const definition = device ? metadata.catalog?.types[device.type] : undefined;
  useEffect(
    () => setTab(key && key === selected ? 'device' : 'spatial'),
    [selected, key],
  );
  const node = manifest.nodes.find((node) => node.entityKey === selected);
  const joint = manifest.joints.find((joint) => joint.entityKey === selected);
  const site = manifest.sites.find(
    (site) =>
      site.entityKey === selected || site.entityKey === joint?.anchorSiteKey,
  );
  const readSite = (site: SceneManifest['sites'][number]): Pose | undefined => {
    if (!site.parentBodyKey) return site.localPose;
    const parent = client.buffer.readLatest(site.parentBodyKey);
    return parent ? multiplyPose(parent, site.localPose) : undefined;
  };
  const pose = site
    ? readSite(site)
    : node?.kind === 'static'
      ? node.initialPose
      : node?.kind === 'body'
        ? client.buffer.readLatest(node.entityKey)
        : undefined;
  const sample = client.buffer.latestSample;
  const binding = node?.kind === 'body' ? node.debug : undefined;
  const error = joint
    ? jointPositionError(
        joint,
        joint.parentBodyKey
          ? client.buffer.readLatest(joint.parentBodyKey)
          : undefined,
        site ? readSite(site) : undefined,
      )
    : undefined;
  useEffect(() => {
    let active = true;
    setJointState(undefined);
    if (joint)
      request(
        `/sessions/${encodeURIComponent(client.sessionId)}/joints/state?key=${encodeURIComponent(joint.entityKey)}`,
      )
        .then((result) => {
          if (active) setJointState(result);
        })
        .catch((error: Error) => {
          if (active) setJointState({ unavailable: error.message });
        });
    return () => {
      active = false;
    };
  }, [joint, client]);
  return (
    <aside className="inspector-panel">
      <div className="panel-heading">
        <h2>Inspector</h2>
        <span className="inspect-icon">⌖</span>
      </div>
      {device && (
        <div
          className="inspector-tabs"
          role="tablist"
          aria-label="Inspector view"
        >
          <button
            role="tab"
            aria-selected={tab === 'spatial'}
            onClick={() => setTab('spatial')}
          >
            Spatial
          </button>
          <button
            role="tab"
            aria-selected={tab === 'device'}
            onClick={() => setTab('device')}
          >
            Device
          </button>
        </div>
      )}
      {tab === 'device' && device && definition ? (
        <EntityPanel
          key={device.key}
          store={client.info}
          binding={device}
          definition={definition}
          disabled={
            state.connection !== 'connected' ||
            state.role !== 'operator' ||
            !metadata.ready ||
            !['RUNNING', 'PAUSED'].includes(state.sessionState)
          }
          onAction={(key, action, params) => client.action(key, action, params)}
        />
      ) : !selected ? (
        <div className="inspector-empty">
          <div>⌖</div>
          <h3>Explore a world coordinate</h3>
          <p>
            Select a body, site, or joint in the scene or explorer to inspect
            its authoritative pose.
          </p>
        </div>
      ) : (
        <div className="inspector-content">
          <span className="eyebrow">
            {joint
              ? 'JOINT ANCHOR'
              : site
                ? `${site.role} · SITE`
                : node?.kind.toUpperCase()}
          </span>
          <h3>{node?.name ?? selected.split('/').at(-1)}</h3>
          <code className="entity-key">{selected}</code>
          {!pose && (node?.kind === 'body' || site || joint) && (
            <p className="field-note" data-testid="pose-unavailable">
              Waiting for the first valid pose.
            </p>
          )}
          {pose && (
            <section className="inspector-section">
              <div className="section-label">
                WORLD POSITION <span>m</span>
              </div>
              <div className="vector-values">
                {pose.p.map((value, i) => (
                  <div key={i}>
                    <span className={`axis axis-${i}`}>
                      {['X', 'Y', 'Z'][i]}
                    </span>
                    <output data-testid={`world-${['x', 'y', 'z'][i]}`}>
                      {format(value)}
                    </output>
                  </div>
                ))}
              </div>
              <div className="section-label">
                QUATERNION <span>xyzw</span>
              </div>
              <div className="quaternion-values">
                {pose.q.map((value, i) => (
                  <div key={i}>
                    <span>{['X', 'Y', 'Z', 'W'][i]}</span>
                    <output data-testid={`quaternion-${i}`}>
                      {format(value)}
                    </output>
                  </div>
                ))}
              </div>
              <p className="field-note">
                Latest authoritative sample
                {site ? ' · Body × Site local pose' : ''}
              </p>
              <dl className="inspector-dl">
                <div>
                  <dt>simTime</dt>
                  <dd data-testid="inspector-sim-time">
                    {sample?.simTime.toFixed(6) ?? '—'} s
                  </dd>
                </div>
                <div>
                  <dt>Sequence</dt>
                  <dd data-testid="inspector-seq">{sample?.seq ?? '—'}</dd>
                </div>
                <div>
                  <dt>sampleTimeMs</dt>
                  <dd>{sample?.sampleTimeMs.toFixed(2) ?? '—'}</dd>
                </div>
                <div>
                  <dt>Epoch</dt>
                  <dd>{sample?.epoch ?? manifest.epoch}</dd>
                </div>
              </dl>
            </section>
          )}
          {site && (
            <section className="inspector-section">
              <div className="section-label">SITE BINDING</div>
              <dl className="inspector-dl">
                <div>
                  <dt>Parent body</dt>
                  <dd>{site.parentBodyKey?.split('/').at(-1) ?? 'World'}</dd>
                </div>
                <div>
                  <dt>Role</dt>
                  <dd>{site.role}</dd>
                </div>
              </dl>
              <p className="field-note">
                This marker is a Newton Site. It is distinct from the body
                origin.
              </p>
            </section>
          )}
          {joint && (
            <section className="inspector-section">
              <div className="section-label">JOINT CONSTRAINT</div>
              <dl className="inspector-dl">
                <div>
                  <dt>Type / DOF</dt>
                  <dd>
                    {joint.type} / {joint.dofCount}
                  </dd>
                </div>
                <div>
                  <dt>Position error</dt>
                  <dd>
                    {error === undefined
                      ? 'Unsupported / not applicable'
                      : `${error.toExponential(3)} m`}
                  </dd>
                </div>
              </dl>
              {jointState !== undefined && (
                <pre>{JSON.stringify(jointState, null, 2)}</pre>
              )}
            </section>
          )}
          {node?.kind === 'body' && (
            <section className="inspector-section">
              <div className="section-label">NEWTON BINDING</div>
              <dl className="inspector-dl">
                <div>
                  <dt>Stream ID</dt>
                  <dd>{node.streamId}</dd>
                </div>
                <div>
                  <dt>Stable key</dt>
                  <dd>{node.stableKey ? 'Yes' : 'No · generated label'}</dd>
                </div>
              </dl>
              {binding && <pre>{JSON.stringify(binding, null, 2)}</pre>}
            </section>
          )}
          {node?.kind === 'group' && (
            <p className="field-note">
              Logical group. Its members retain independent world transforms.
            </p>
          )}
        </div>
      )}
    </aside>
  );
}
