import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import type { Catalog, SceneManifest } from '../lib/protocol';
import { usePlatform } from '../lib/use-platform';
import { bytes, percent, type PlatformSession, type PlatformSnapshot } from '../lib/platform';
import { deviceKey } from '../lib/info/binding';
import { SessionClient } from '../lib/stream/client';
import { ReplaySource } from '../lib/replay/replay-source';
import { request } from '../lib/api';
import { readRecording, recordedScene, type Recording } from '../lib/replay/reader';
import { FrameRecorder } from '../lib/performance/frame-recorder';
import { SceneViewport, type InspectVisuals, type RenderedPose, type RenderMetrics } from './viewer/SceneViewport';
import { EntityPanel } from './EntityPanel';
import { useInvocations } from '../lib/info/hooks';

const value = (input: unknown) => typeof input === 'string' ? input : JSON.stringify(input);
const title = (manifest: SceneManifest, catalog: Catalog, key: string) =>
  manifest.nodes.find(node => node.entityKey === key)?.name ?? catalog.types[catalog.entities[key]?.type]?.title ?? key.split('/').at(-1) ?? key;

function Inspector({ snapshot, session, recording, deviceCount }: {
  snapshot?: PlatformSnapshot; session?: PlatformSession; recording?: Recording; deviceCount?: number;
}) {
  const gpu = snapshot?.system.gpus[0];
  return <aside className="overview-inspector-panel panel" aria-label="检查器">
    <section><span className="eyebrow">{recording ? '预览录制' : '活动会话'}</span><dl className="reference-kv">
      <div><dt>会话</dt><dd>{session || recording ? <code>{session?.sessionId ?? recording?.sessionId}</code> : '无活动会话'}</dd></div>
      <div><dt>场景</dt><dd>{session?.sceneName ?? recording?.sceneId ?? '—'}</dd></div>
      <div><dt>场景版本</dt><dd><code>{session ? session.sceneVersion ?? session.sceneId : recording ? recordedScene(recording) : '—'}</code></dd></div>
      <div><dt>物理源</dt><dd>{session?.source ?? '—'}</dd></div>
      <div><dt>设备</dt><dd>{session?.backend?.device ?? (session?.source === 'synthetic' ? 'CPU · Synthetic' : '—')}</dd></div>
      <div><dt>已绑定实体</dt><dd>{deviceCount ?? '—'}</dd></div>
      <div><dt>观察者</dt><dd>{session?.connectedClients ?? '—'}</dd></div>
    </dl>{session && <Link to="/sessions/$sessionId" params={{ sessionId: session.sessionId }} className="reference-link">打开会话 →</Link>}</section>
    <section><span className="eyebrow">主机资源</span><dl className="reference-kv">
      <div><dt>CPU</dt><dd>{percent(snapshot?.system.cpu.percent)}</dd></div>
      <div><dt>内存</dt><dd>{bytes(snapshot?.system.memory.usedBytes)} / {bytes(snapshot?.system.memory.totalBytes)}</dd></div>
      <div><dt>GPU</dt><dd>{gpu?.name ?? snapshot?.system.gpuMessage ?? '未取得读数'}</dd></div>
      {gpu && <><div><dt>显存</dt><dd>{bytes(gpu.memoryUsedBytes)} / {bytes(gpu.memoryTotalBytes)}</dd></div>
        <div className="reference-meter" role="img" aria-label={`GPU memory ${percent(gpu.memoryUsedBytes != null && gpu.memoryTotalBytes ? gpu.memoryUsedBytes / gpu.memoryTotalBytes * 100 : null)}`}>
          <i style={{ width: `${gpu.memoryUsedBytes != null && gpu.memoryTotalBytes ? Math.min(100, gpu.memoryUsedBytes / gpu.memoryTotalBytes * 100) : 0}%` }} /></div></>}
    </dl><Link to="/resources/gpu" className="reference-link">查看算力 →</Link></section>
  </aside>;
}

function teachingLocations(catalog: Catalog, key: string) {
  const names = new Set<string>();
  for (const binding of Object.values(catalog.entities)) {
    const config = binding.config.locations;
    if (!config || typeof config !== 'object' || Array.isArray(config)) continue;
    const locations = config.locations;
    if (!locations || typeof locations !== 'object' || Array.isArray(locations)) continue;
    for (const [name, location] of Object.entries(locations)) {
      if (location && typeof location === 'object' && !Array.isArray(location) && typeof location.site === 'string' && location.site.startsWith(`${key}/`)) names.add(name);
    }
  }
  return [...names];
}

function DeviceDrawer({ client, manifest, catalog, selected, select }: {
  client: SessionClient; manifest: SceneManifest; catalog?: Catalog; selected: string; select: (key: string) => void;
}) {
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const binding = catalog?.entities[selected];
  const definition = binding && catalog?.types[binding.type];
  const locations = catalog && binding ? teachingLocations(catalog, binding.key) : [];
  const actionable = Object.values(catalog?.entities ?? {}).filter(binding => Object.keys(catalog?.types[binding.type]?.actions ?? {}).length);
  return <aside className="overview-device-drawer" aria-label="设备抽屉" data-connection={state.connection} data-role={state.role} data-state={state.sessionState} data-ready={client.info.getMetadata().ready} data-read-only={client.readOnly}>
    {binding && definition ? <>
      <div className="drawer-heading"><span className="eyebrow">设备</span><button className="reference-link" aria-label="关闭设备抽屉" onClick={() => select('')}>× 关闭</button></div>
      <h2>{title(manifest, catalog!, selected)}</h2><code className="drawer-key">{selected}</code>
      <dl className="reference-kv"><div><dt>实体类型</dt><dd><code>{binding.type}</code></dd></div>
        <div><dt>属性</dt><dd><code>{Object.keys(definition.properties).join(' · ')}</code></dd></div>
        <div><dt>素材</dt><dd>未关联素材</dd></div><div><dt>设备程序</dt><dd><code>{binding.program}</code></dd></div></dl>
      {locations.length > 0 && <section><span className="eyebrow">示教位置</span><p className="teaching-locations">{locations.map(name => <code key={name}>{name}</code>)}</p></section>}
      <EntityPanel store={client.info} binding={binding} definition={definition} compact
        disabled={client.readOnly || state.connection !== 'connected' || state.role !== 'operator' || !client.info.getMetadata().ready || !['RUNNING', 'PAUSED'].includes(state.sessionState)}
        onAction={(key, action, params) => client.action(key, action, params)} />
    </> : <>
      <span className="eyebrow">设备抽屉</span><h2>点击地图上的设备</h2><p className="drawer-instruction">查看它的实体类型、动作与示教位置。</p>
      <span className="eyebrow">图例</span>
      <div className="device-legend">{actionable.map(binding => <button key={binding.key} onClick={() => select(binding.key)} data-testid={`legend-${binding.key}`}>
        <i /><span>{title(manifest, catalog!, binding.key)}</span><code>{binding.type}</code>
      </button>)}
        {catalog && Object.keys(catalog.entities).length > actionable.length && <details><summary>其他实体 · {Object.keys(catalog.entities).length - actionable.length}</summary>
          {Object.values(catalog.entities).filter(binding => !actionable.includes(binding)).map(binding => <button key={binding.key} data-testid={`legend-${binding.key}`} onClick={() => select(binding.key)}>
            <i /><span>{title(manifest, catalog, binding.key)}</span><code>{binding.type}</code></button>)}
        </details>}
        {!catalog && <p className="drawer-instruction">正在读取实体目录…</p>}
      </div>
    </>}
  </aside>;
}

function Timeline({ client, manifest, catalog }: { client: SessionClient; manifest?: SceneManifest; catalog?: Catalog }) {
  const activity = useSyncExternalStore(client.info.subscribeActivity, client.info.getActivity);
  const invocations = useInvocations(client.info);
  const entries = activity.length ? activity : invocations.slice(0, 8).map(invocation => ({
    id: invocation.invocationId, label: `${invocation.action} · ${invocation.status}`, simTime: invocation.endedSimTime ?? invocation.startedSimTime ?? invocation.createdSimTime,
    entityKey: invocation.entityKey, actor: invocation.actor.kind === 'agent' ? 'agent' : 'human',
  }));
  return <section className="reference-panel activity-panel"><header><h2>近期活动</h2><span className="eyebrow">会话事件</span></header>
    {entries.length ? <ol className="activity-timeline">{entries.slice(0, 12).map(entry => <li key={entry.id} className={`activity-${entry.actor}`}>
      <time>{entry.simTime.toFixed(2)} s</time><div><strong>{entry.label}</strong><span>{manifest && catalog ? title(manifest, catalog, entry.entityKey) : entry.entityKey}</span></div>
    </li>)}</ol> : <div className="timeline-empty">还没有动作或设备事件。选择设备后执行动作，活动会显示在这里。</div>}
  </section>;
}

function SessionOverview({ session, snapshot, recording }: { session?: PlatformSession; snapshot?: PlatformSnapshot; recording?: Recording }) {
  const sessionId = session?.sessionId ?? recording!.sessionId;
  const client = useMemo(() => session ? new SessionClient(sessionId) : new ReplaySource(sessionId), [sessionId, !!session]);
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const metadata = useSyncExternalStore(client.info.subscribeMetadata, client.info.getMetadata);
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const search = useSearch({ strict: false });
  const selected = search.entity ?? '';
  const readouts = search.readouts !== false;
  const revision = useRef<string | undefined>(undefined);
  const metrics = useRef<RenderMetrics>({ fps: 0, drawCalls: 0, objects: 0, applyMs: 0 });
  const rendered = useRef(new Map<string, RenderedPose>());
  const inspectVisuals = useRef<InspectVisuals | undefined>(undefined);
  const recorder = useMemo(() => new FrameRecorder(), []);
  const hidden = useMemo(() => new Set<string>(), []);
  const warn = useCallback(() => {}, []);
  useEffect(() => {
    let alive = true;
    void client.start().catch((error: Error) => { if (alive) setError(error.message); });
    return () => { alive = false; client.stop(); };
  }, [client]);
  useEffect(() => {
    const next = state.manifest?.sceneRevision;
    if (next && revision.current && revision.current !== next)
      void navigate({ to: '/', search: previous => ({ ...previous, entity: undefined }) });
    if (next) revision.current = next;
  }, [state.manifest?.sceneRevision, navigate]);
  const manifest = state.manifest;
  const catalog = metadata.catalog;
  const choose = (key: string) => {
    const owner = manifest ? deviceKey(manifest, catalog, key) ?? key : key;
    void navigate({ to: '/', search: previous => ({ ...previous, entity: selected === owner ? undefined : owner || undefined }) });
  };
  const deviceBodies = useMemo(() => {
    const result = new Map<string, string[]>();
    if (manifest && catalog) for (const node of manifest.nodes) {
      if (node.kind === 'group') continue;
      const owner = deviceKey(manifest, catalog, node.entityKey);
      if (!owner) continue;
      const keys = result.get(owner) ?? [];
      keys.push(node.entityKey); result.set(owner, keys);
    }
    return result;
  }, [manifest, catalog]);
  const highlightedKeys = useMemo(() => new Set(deviceBodies.get(selected) ?? []), [deviceBodies, selected]);
  const sceneLabel = session ? session.sceneVersion ?? session.sceneId : recordedScene(recording!);
  const annotations = manifest && catalog ? Object.values(catalog.entities)
    .filter(binding => Object.keys(catalog.types[binding.type]?.actions ?? {}).length > 0 || binding.key === selected)
    .map(binding => {
      const definition = catalog.types[binding.type];
      const values = client.info.getEntity(binding.key)?.values ?? {};
      const details = Object.entries(values).filter(([, v]) => typeof v === 'number' || typeof v === 'boolean' || typeof v === 'string').slice(0, 3)
        .map(([name, v]) => `${name} ${value(v)}${definition?.properties[name]?.unit ? ` ${definition.properties[name].unit}` : ''}`).join(' · ');
      return { key: binding.key, label: title(manifest, catalog, binding.key), detail: readouts ? details : '', bodyKeys: deviceBodies.get(binding.key) ?? [] };
    }) : [];
  return <>
    <header className="overview-page-heading"><h1>概览</h1><p><code title={recording?.sceneContentHash ?? undefined}>{sceneLabel}</code><span>·</span><code>{sessionId}</code><span>·</span>
      <span className="reference-chip"><i />{client.readOnly ? '最近录制 · 只读预览' : `${state.sessionState} · ${state.simTime.toFixed(2)} s`}</span></p></header>
    {error || state.error || state.infoError ? <div role="alert" className="platform-error">{error || state.error || state.infoError}</div> : null}
    <div className="overview-hero">
      <figure className="overview-figure">
        <div className="overview-stage">
          {manifest ? <SceneViewport manifest={manifest} client={client} selected={selected} select={choose}
            layers={{ colliders: false, sites: false, origins: false, grid: true }} hidden={hidden} warn={warn}
            metrics={metrics} rendered={rendered} inspectVisuals={inspectVisuals} recorder={recorder}
            cameraPreset="isometric" annotations={annotations} highlightedKeys={highlightedKeys} compact renderContinuously={!client.readOnly} /> :
            <div className="overview-loading" role="status">正在读取空间状态…</div>}
        </div>
        <figcaption><strong>图 1</strong><span>{manifest?.sceneName ?? '活动会话'} · 点击设备</span>
          <button className="readout-switch" aria-label="读数" aria-pressed={readouts} onClick={() => void navigate({ to: '/', search: previous => ({ ...previous, readouts: readouts ? false : undefined }) })}><i />读数</button>
          {session ? <span className="reference-tag">实时状态</span> : <Link to="/sessions" className="reference-link">新建会话 →</Link>}
        </figcaption>
      </figure>
      {manifest && <DeviceDrawer {...{ client, manifest, catalog, selected }} select={choose} />}
    </div>
    <div className="overview-lower"><Timeline {...{ client, manifest, catalog }} /><Inspector snapshot={snapshot} session={session} recording={recording} deviceCount={catalog ? Object.keys(catalog.entities).length : undefined} /></div>
  </>;
}

export function Overview() {
  const { snapshot, error } = usePlatform();
  const active = snapshot?.sessions.find(session => ['RUNNING', 'PAUSED'].includes(session.state));
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [recordingError, setRecordingError] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    void request<unknown[]>('/recordings', { signal: controller.signal })
      .then(values => setRecordings(values.map(readRecording)))
      .catch((error: Error) => { if (!controller.signal.aborted) setRecordingError(error.message); });
    return () => controller.abort();
  }, []);
  const recent = snapshot ? recordings.find(recording => recording.kind === 'session'
    && recording.segments.length > 0 && recording.status !== 'recording') : undefined;
  return <main className="overview-page">
    {recordingError && <div role="alert" className="platform-error">无法读取最近录制：{recordingError}</div>}
    {active || recent ? <SessionOverview key={active?.sessionId ?? recent!.sessionId} session={active} snapshot={snapshot} recording={active ? undefined : recent} /> : <>
      <header className="overview-page-heading"><h1>概览</h1><p>共享实验室 · 一个权威空间状态</p></header>
      {error && <div role="alert" className="platform-error">{error}</div>}
      <div className="overview-hero"><figure className="overview-figure"><div className="overview-stage empty-overview-stage"><span className="empty-scene-mark" aria-hidden="true">◇</span>
        <h2>{snapshot ? '开始一次实验' : '正在连接实验室'}</h2><p>启动场景后，在这里查看设备、实时读数与动作。</p><Link className="button-link primary" to="/sessions">新建会话 →</Link></div>
        <figcaption><strong>图 1</strong><span>实验室空间视图</span></figcaption></figure><aside className="overview-device-drawer"><span className="eyebrow">设备抽屉</span><h2>等待活动会话</h2><p>会话启动后，点击设备查看它的状态、动作与示教位置。</p></aside></div>
      <div className="overview-lower"><section className="reference-panel activity-panel"><header><h2>近期活动</h2></header><div className="timeline-empty">启动会话后，动作与设备事件会出现在这里。</div></section><Inspector snapshot={snapshot} /></div>
    </>}
  </main>;
}
