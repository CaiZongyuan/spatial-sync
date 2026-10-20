import { useEffect, useState, useSyncExternalStore } from 'react';
import { deviceKey } from '../lib/info/binding';
import { EntityPanel } from './EntityPanel';
import type { SceneManifest, Pose } from '../lib/protocol';
import type { SessionClient, ViewState } from '../lib/stream/client';
import { multiplyPose } from '../lib/scene/pose';
import { jointPositionError } from '../lib/scene/joint-error';
import { request } from '../lib/api';
import { Icon } from './Icon';

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
  const [copied, setCopied] = useState<string>();
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
    if (joint && !client.readOnly)
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
        <h2>检查器</h2>
        <Icon name="site" className="inspect-icon" />
      </div>
      {device && (
        <div
          className="inspector-tabs"
          role="tablist"
          aria-label="Inspector view"
          onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
            event.preventDefault(); setTab(tab === 'spatial' ? 'device' : 'spatial');
            const tabs = event.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]');
            tabs[tab === 'spatial' ? 1 : 0]?.focus();
          } }}
        >
          <button
            role="tab"
            aria-label="Spatial"
            aria-selected={tab === 'spatial'}
            onClick={() => setTab('spatial')}
          >
            空间
          </button>
          <button
            role="tab"
            aria-label="Device"
            aria-selected={tab === 'device'}
            onClick={() => setTab('device')}
          >
            设备 · {manifest.nodes.find(node => node.entityKey === key)?.name ?? key?.split('/').at(-1)}
          </button>
        </div>
      )}
      {tab === 'device' && device && definition ? (
        <EntityPanel
          key={device.key}
          store={client.info}
          binding={device}
          definition={definition}
          disabledReason={client.readOnly ? 'Recorded sessions are read-only.' : undefined}
          disabled={
            client.readOnly ||
            state.connection !== 'connected' ||
            state.role !== 'operator' ||
            !metadata.ready ||
            !['RUNNING', 'PAUSED'].includes(state.sessionState)
          }
          onAction={(key, action, params) => client.action(key, action, params)}
        />
      ) : !selected ? (
        <div className="inspector-empty">
          <Icon name="site" className="inspector-empty-icon" />
          <h3>查看世界坐标</h3>
          <p>
            在场景或场景树中选择刚体、站点或关节，查看物理源提供的最新位姿。
          </p>
        </div>
      ) : (
        <div className="inspector-content">
          <span className="eyebrow">
            {joint
              ? '关节锚点'
              : site
                ? `${site.role} · 站点`
                : node?.kind === 'body' ? '刚体' : node?.kind === 'static' ? '静态物体' : '逻辑组'}
          </span>
          <h3>{node?.name ?? selected.split('/').at(-1)}</h3>
          <div className="inspector-path"><code className="entity-key">{selected}</code><button aria-label="Copy entity path"
            title={copied === selected ? '已复制' : '复制路径'} onClick={async () => {
              try { await navigator.clipboard.writeText(selected); setCopied(selected); } catch { setCopied(undefined); }
            }}><Icon name={copied === selected ? 'check' : 'copy'} /></button></div>
          {!pose && (node?.kind === 'body' || site || joint) && (
            <p className="field-note" data-testid="pose-unavailable">
              等待首个有效位姿。
            </p>
          )}
          {pose && (
            <section className="inspector-section">
              <div className="section-label">
                世界位置 <span>m</span>
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
                四元数 <span>xyzw</span>
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
                物理源的最新样本
                {site ? ' · 刚体 × 站点局部位姿' : ''}
              </p>
              <dl className="inspector-dl">
                <div>
                  <dt>simTime</dt>
                  <dd data-testid="inspector-sim-time">
                    {sample?.simTime.toFixed(6) ?? '—'} s
                  </dd>
                </div>
                <div>
                  <dt>序号</dt>
                  <dd data-testid="inspector-seq">{sample?.seq ?? '—'}</dd>
                </div>
                <div>
                  <dt>sampleTimeMs</dt>
                  <dd>{sample?.sampleTimeMs.toFixed(2) ?? '—'}</dd>
                </div>
                <div>
                  <dt>轮次</dt>
                  <dd>{sample?.epoch ?? manifest.epoch}</dd>
                </div>
              </dl>
            </section>
          )}
          {site && (
            <section className="inspector-section">
              <div className="section-label">站点绑定</div>
              <dl className="inspector-dl">
                <div>
                  <dt>父级刚体</dt>
                  <dd>{site.parentBodyKey?.split('/').at(-1) ?? '世界'}</dd>
                </div>
                <div>
                  <dt>角色</dt>
                  <dd>{site.role}</dd>
                </div>
              </dl>
              <p className="field-note">
                该标记为 Newton 站点；它的位置可与刚体原点不同。
              </p>
            </section>
          )}
          {joint && (
            <section className="inspector-section">
              <div className="section-label">关节约束</div>
              <dl className="inspector-dl">
                <div>
                  <dt>类型 / 自由度</dt>
                  <dd>
                    {joint.type} / {joint.dofCount}
                  </dd>
                </div>
                <div>
                  <dt>位置误差</dt>
                  <dd>
                    {error === undefined
                      ? '不支持或不适用'
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
              <div className="section-label">Newton 绑定</div>
              <dl className="inspector-dl">
                <div>
                  <dt>流 ID</dt>
                  <dd>{node.streamId}</dd>
                </div>
                <div>
                  <dt>稳定标识</dt>
                  <dd>{node.stableKey ? '是' : '否 · 自动标签'}</dd>
                </div>
              </dl>
              {binding && <pre>{JSON.stringify(binding, null, 2)}</pre>}
            </section>
          )}
          {node?.kind === 'group' && (
            <p className="field-note">
              逻辑组的成员各自保留独立的世界变换。
            </p>
          )}
        </div>
      )}
    </aside>
  );
}
