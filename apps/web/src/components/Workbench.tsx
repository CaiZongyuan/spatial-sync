import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import type { ReactNode } from 'react';
import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { SessionClient } from '../lib/stream/client';
import { request } from '../lib/api';
import { FrameRecorder } from '../lib/performance/frame-recorder';
import { SceneTree } from './SceneTree';
import { Inspector } from './Inspector';
import { ApprovalBanner, InvocationsPanel } from './InvocationsPanel';
import { Diagnostics } from './Diagnostics';
import { NewtonViewerButton } from './NewtonViewerButton';
import { Icon } from './Icon';
import { connectionLabel, sessionLabel } from '../lib/ui-labels';
import '../workbench.css';
import {
  SceneViewport,
  type DebugLayers,
  type RenderMetrics,
  type RenderedPose,
  type InspectVisuals,
} from './viewer/SceneViewport';

declare global {
  interface Window {
    __spatialDebug?: {
      sessionId: string;
      getReplaySource: () => SessionClient | undefined;
      getRenderedPose: (key: string) => RenderedPose | undefined;
      getAuthoritativePose: SessionClient['buffer']['readLatest'];
      getSample: () => SessionClient['buffer']['latestSample'];
      getEntityState: SessionClient['info']['getEntity'];
      getConnection: () => string;
      getDiscardedFrames: () => number;
      inspectVisuals: InspectVisuals;
      startProfile: () => ReturnType<SessionClient['getStreamMetrics']>;
      stopProfile: FrameRecorder['stop'];
      getStreamMetrics: SessionClient['getStreamMetrics'];
    };
  }
}

export function Workbench({ sessionId, source, controls, renderContinuously = true }: {
  sessionId: string;
  source?: SessionClient;
  controls?: ReactNode;
  renderContinuously?: boolean;
}) {
  const navigate = useNavigate();
  const client = useMemo(() => source ?? new SessionClient(sessionId), [sessionId, source]);
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const [loadError, setLoadError] = useState<string>();
  const [operationPending, setOperationPending] = useState(false);
  const [selected, setSelected] = useState('');
  const search = useSearch({ strict: false });
  const cameraPreset = search.camera ?? 'perspective';
  const setCameraPreset = (camera: 'perspective' | 'isometric') => void navigate({
    to: '.', search: previous => ({ ...previous, camera: camera === 'isometric' ? camera : undefined }),
  });
  const [tab, setTab] = useState<'scene' | 'diagnostics'>('scene');
  const [layers, setLayers] = useState<DebugLayers>({
    colliders: false,
    sites: true,
    origins: false,
    grid: true,
  });
  const [hidden, setHidden] = useState(new Set<string>());
  const [warnings, setWarnings] = useState<string[]>([]);
  const metrics = useRef<RenderMetrics>({
    fps: 0,
    drawCalls: 0,
    objects: 0,
    applyMs: 0,
  });
  const rendered = useRef(new Map<string, RenderedPose>());
  const inspectVisuals = useRef<InspectVisuals | undefined>(undefined);
  const recorder = useMemo(() => new FrameRecorder(), []);
  const warn = useCallback(
    (message: string) =>
      setWarnings((warnings) =>
        warnings.includes(message) ? warnings : [...warnings, message],
      ),
    [],
  );
  useEffect(() => {
    let active = true;
    setLoadError(undefined);
    void client.start().catch((error: Error) => {
      if (active) setLoadError(error.message);
    });
    window.__spatialDebug = {
      sessionId,
      getReplaySource: () => client.readOnly ? client : undefined,
      getRenderedPose: (key) => rendered.current.get(key),
      getAuthoritativePose: (key) => client.buffer.readLatest(key),
      getSample: () => client.buffer.latestSample,
      getEntityState: (key) => client.info.getEntity(key),
      getConnection: () => client.getSnapshot().connection,
      getDiscardedFrames: () => client.buffer.discardedFrames,
      inspectVisuals: (key) => inspectVisuals.current?.(key),
      startProfile: () => {
        recorder.start();
        return client.getStreamMetrics();
      },
      stopProfile: () => recorder.stop(),
      getStreamMetrics: client.getStreamMetrics,
    };
    return () => {
      active = false;
      client.stop();
      delete window.__spatialDebug;
    };
  }, [client, sessionId, recorder]);
  useEffect(() => {
    setSelected('');
    setHidden(new Set());
    setWarnings([]);
    rendered.current.clear();
  }, [state.manifest?.sceneRevision]);
  const manifest = state.manifest;
  const effectiveHidden = useMemo(() => {
    const result = new Set(hidden);
    if (!manifest) return result;
    // Logical group visibility propagates without inheriting its transform.
    for (let pass = 0; pass < manifest.nodes.length; pass++) {
      let changed = false;
      for (const node of manifest.nodes)
        if (
          node.parentEntityKey &&
          result.has(node.parentEntityKey) &&
          !result.has(node.entityKey)
        ) {
          result.add(node.entityKey);
          changed = true;
        }
      if (!changed) break;
    }
    for (const joint of manifest.joints)
      if (result.has(joint.entityKey) && joint.anchorSiteKey)
        result.add(joint.anchorSiteKey);
    return result;
  }, [hidden, manifest]);
  const pending = state.pending.length > 0 || operationPending;
  const connected = state.connection === 'connected';
  const canControl = !client.readOnly && connected && state.role === 'operator';
  const info = useSyncExternalStore(
    client.info.subscribeMetadata,
    client.info.getMetadata,
  );
  const actionsDisabled =
    !canControl ||
    !info.ready ||
    !['RUNNING', 'PAUSED'].includes(state.sessionState);
  const paused = state.sessionState === 'PAUSED';
  const active = paused || state.sessionState === 'RUNNING';
  const warningCount = (manifest?.warnings.length ?? 0) + warnings.length;
  const age = Math.max(0, performance.now() - client.buffer.receivedAt);
  const lifecycle = async (action: 'reload' | 'restart') => {
    setOperationPending(true);
    setLoadError(undefined);
    try {
      await request(`/sessions/${encodeURIComponent(sessionId)}/${action}`, {
        method: 'POST',
      });
    } catch (error) {
      setLoadError((error as Error).message);
    } finally {
      setOperationPending(false);
    }
  };
  return (
    <main className="workbench">
      <header className="workbench-header">
        <Link to="/sessions" className="brand" title="Choose scene or session">
          <span className="eyebrow">会话工作台</span>
        </Link>
        <div className="workspace-title">
          <strong>{manifest?.sceneName ?? '正在打开会话…'}</strong>
          <span>{sessionId}</span>
        </div>
        <div className="header-status">
          <span className={`connection-dot ${connected ? 'connected' : ''}`} />
          <span data-testid="connection-status" data-connection={state.connection} title={state.connection}>{connectionLabel(state.connection)}</span>
          <span className="source-pill">
            {manifest?.source.kind ?? 'source'}
          </span>
          <span className="observer-count">{Array.isArray(state.stats?.clients) ? state.stats.clients.length : '—'} 位观察者</span>
            <Link to={client.readOnly ? '/recordings' : '/sessions'} className="subtle-link" aria-label={client.readOnly ? 'Recordings' : 'Sessions'}>
            {client.readOnly ? '录制列表' : '会话列表'}<Icon name="external" />
          </Link>
          {!client.readOnly && <NewtonViewerButton sessionId={sessionId}
            source={manifest?.source.kind ?? ''} state={state.sessionState} sceneRevision={manifest?.sceneRevision} />}
          <button
            aria-label="Close session"
            disabled={client.readOnly || pending || state.role !== 'operator'}
            onClick={async () => {
              try {
                await request<void>(
                  `/sessions/${encodeURIComponent(sessionId)}`,
                  { method: 'DELETE' },
                );
                await navigate({ to: '/sessions' });
              } catch (error) {
                setLoadError((error as Error).message);
              }
            }}
          >
            <Icon name="close" />关闭会话
          </button>
        </div>
      </header>
      {controls ?? <div className="control-bar">
        <div className="playback-controls">
          <span
            className={`state-pill ${state.sessionState.toLowerCase()}`}
            data-testid="session-state"
            data-state={state.sessionState}
            title={state.sessionState}
          >
            {sessionLabel(state.sessionState)}
          </span>
          <button
            disabled={!canControl || pending || !active}
            onClick={() => client.command(paused ? 'resume' : 'pause')}
            aria-label={paused ? 'Resume simulation' : 'Pause simulation'}
          >
            <Icon name={paused ? 'play' : 'pause'} />{paused ? '继续' : '暂停'}
          </button>
          <button
            disabled={!canControl || pending || !paused}
            onClick={() => client.command('step', { count: 1 })}
            aria-label="Step simulation"
          >
            <Icon name="step" />单步
          </button>
          <button
            disabled={!canControl || pending || !active}
            onClick={() => client.command('reset')}
            aria-label="Reset simulation"
          >
            <Icon name="reset" />重置
          </button>
          <button
            aria-label="Reload scene"
            disabled={pending || !active || state.role !== 'operator'}
            onClick={() => void lifecycle('reload')}
          >
            <Icon name="reload" />重新载入场景
          </button>
          {state.sessionState === 'FAILED' && (
            <button
              aria-label="Restart source"
              disabled={pending || state.role !== 'operator'}
              onClick={() => void lifecycle('restart')}
            >
              <Icon name="reload" />重启物理源
            </button>
          )}
        </div>
        <div className="timing-control">
          <span className="muted">simTime</span>
          <output data-testid="sim-time">
            {state.simTime.toFixed(4)}
            <small>s</small>
          </output>
          <div className="rtf-picker"><span>RTF</span><div className="rtf-segment" role="group" aria-label="Realtime factor">
            {([.25, .5, 1, 2, null] as const).map(value => <button key={value ?? 'unlimited'}
              aria-label={`Realtime factor ${value ?? 'Unlimited'}`} aria-pressed={state.realtimeFactor === value}
              disabled={!canControl || pending || !active} onClick={() => client.command('set_realtime_factor', { value })}>
              {value === null ? '不限' : `${value}×`}</button>)}
          </div></div>
        </div>
      </div>}
      <ApprovalBanner
        store={client.info}
        disabled={actionsDisabled}
        onDecision={(id, decision) => client.approveInvocation(id, decision)}
      />
      {(loadError || state.error || state.infoError) && (
        <div role="alert" className="banner error">
          {loadError ?? state.error ?? state.infoError}
        </div>
      )}
      {(pending || state.commandResult) && (
        <div role="status" className="command-notice">
          {pending
            ? '等待物理源确认…'
            : state.commandResult === 'Command applied' ? '命令已应用' : state.commandResult}
        </div>
      )}
      {!manifest ? (
        <div className="loading-workspace">
          <div className="loading-orbit" />
          <h2>
            {loadError
              ? '网关暂不可用'
              : '正在加载空间工作台'}
          </h2>
          <p>
            {loadError ??
              'Reading the scene manifest and establishing the transform stream…'}
          </p>
          <Link to="/sessions">Back to sessions</Link>
        </div>
      ) : (
        <div className="workbench-main">
          <SceneTree
            info={client.info}
            manifest={manifest}
            selected={selected}
            select={setSelected}
            hidden={effectiveHidden}
            toggle={(key) =>
              setHidden((previous) => {
                const next = new Set(previous);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                return next;
              })
            }
          />
          <section className="scene-panel">
            <div className="scene-toolbar">
              <div className="view-tabs">
                <button
                  aria-label="Spatial view"
                  aria-selected={tab === 'scene'}
                  onClick={() => setTab('scene')}
                >
                  空间视图
                </button>
                <button
                  aria-label="Diagnostics"
                  aria-selected={tab === 'diagnostics'}
                  onClick={() => setTab('diagnostics')}
                >
                  诊断
                  {warningCount > 0 && (
                    <span className="warning-count">{warningCount}</span>
                  )}
                </button>
              </div>
              <div className="layer-toggles">
                <div className="camera-segment" role="group" aria-label="Camera projection">
                  <button aria-label="Perspective camera" aria-pressed={cameraPreset === 'perspective'} onClick={() => setCameraPreset('perspective')}>透视</button>
                  <button aria-label="Isometric camera" aria-pressed={cameraPreset === 'isometric'} onClick={() => setCameraPreset('isometric')}>等距</button>
                </div>
                {(['grid', 'sites', 'origins', 'colliders'] as const).map(
                  (layer) => (
                    <label key={layer}>
                      <input
                        type="checkbox"
                        aria-label={{ grid: 'Grid', sites: 'Sites', origins: 'Origins', colliders: 'Colliders' }[layer]}
                        checked={layers[layer]}
                        onChange={(event) =>
                          setLayers((previous) => ({
                            ...previous,
                            [layer]: event.target.checked,
                          }))
                        }
                      />
                      <span>{{ grid: '网格', sites: '站点', origins: '原点', colliders: '碰撞体' }[layer]}</span>
                    </label>
                  ),
                )}
              </div>
            </div>
            <div className="scene-content">
              <SceneViewport
                {...{
                  manifest,
                  client,
                  selected,
                  layers,
                  hidden: effectiveHidden,
                  select: setSelected,
                  warn,
                  metrics,
                  rendered,
                  inspectVisuals,
                  recorder,
                  renderContinuously,
                  cameraPreset,
                }}
              />
              {tab === 'diagnostics' && (
                <Diagnostics
                  {...{ client, state, metrics: metrics.current, warnings }}
                />
              )}
            </div>
            <InvocationsPanel
              store={client.info}
              {...{ manifest, selected, simTime: state.simTime }}
              disabled={actionsDisabled}
              onCancel={(id) => client.cancelInvocation(id)}
            />
          </section>
          <Inspector {...{ manifest, selected, client, state }} />
        </div>
      )}
      <footer className="status-bar">
        <div>
          <span className="live-dot" />
          {metrics.current.fps.toFixed(0)} <small>渲染帧率</small>
        </div>
        <div>
          {client.buffer.grantedRateHz} Hz <small>状态流</small>
        </div>
        <div>
          {state.actualRtf.toFixed(2)}× <small>实际倍率</small>
        </div>
        <div
          className={
            state.sessionState === 'RUNNING' && age > 1000 ? 'warning-text' : ''
          }
        >
          {state.sessionState === 'RUNNING'
            ? `${age.toFixed(0)} ms`
            : paused
              ? '已暂停'
              : '—'}{' '}
          <small>样本时延</small>
        </div>
        <div>
          轮次{' '}
          <output data-testid="epoch">
            {client.buffer.epoch < 0
              ? (manifest?.epoch ?? '—')
              : client.buffer.epoch}
          </output>
        </div>
        <div>
          序号{' '}
          <output data-testid="sequence">
            {client.buffer.lastSeq < 0 ? '—' : client.buffer.lastSeq}
          </output>
        </div>
        <button
          className="status-warnings"
          aria-label="查看诊断警告"
          onClick={() => setTab('diagnostics')}
        >
          <Icon name={warningCount ? 'warning' : 'check'} />{warningCount ? `${warningCount} 条诊断警告` : '场景兼容'}
        </button>
        <span className="revision-label">
          {manifest?.sceneRevision ?? '等待读取场景'}
        </span>
      </footer>
    </main>
  );
}
