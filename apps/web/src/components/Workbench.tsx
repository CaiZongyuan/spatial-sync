import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { SessionClient } from '../lib/stream/client';
import { request } from '../lib/api';
import { FrameRecorder } from '../lib/performance/frame-recorder';
import { SceneTree } from './SceneTree';
import { Inspector } from './Inspector';
import { SubscriptionControls } from './SubscriptionControls';
import { Diagnostics } from './Diagnostics';
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
      getRenderedPose: (key: string) => RenderedPose | undefined;
      getAuthoritativePose: SessionClient['buffer']['readLatest'];
      getSample: () => SessionClient['buffer']['latestSample'];
      getConnection: () => string;
      getDiscardedFrames: () => number;
      inspectVisuals: InspectVisuals;
      startProfile: () => ReturnType<SessionClient['getStreamMetrics']>;
      stopProfile: FrameRecorder['stop'];
      getStreamMetrics: SessionClient['getStreamMetrics'];
      getSubscriptionMetrics: SessionClient['getSubscriptionMetrics'];
      setSubscription: SessionClient['setSubscription'];
      updateViewport: SessionClient['updateViewport'];
    };
  }
}

export function Workbench({ sessionId }: { sessionId: string }) {
  const navigate = useNavigate();
  const client = useMemo(
    () =>
      new SessionClient(
        sessionId,
        new URLSearchParams(location.search).get('format') === 'binary'
          ? 'binary'
          : 'json',
      ),
    [sessionId],
  );
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const [loadError, setLoadError] = useState<string>();
  const [operationPending, setOperationPending] = useState(false);
  const [selected, setSelected] = useState('');
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
    void client.start().catch((error: Error) => setLoadError(error.message));
    window.__spatialDebug = {
      sessionId,
      getRenderedPose: (key) => rendered.current.get(key),
      getAuthoritativePose: (key) => client.buffer.readLatest(key),
      getSample: () => client.buffer.latestSample,
      getConnection: () => client.getSnapshot().connection,
      getDiscardedFrames: () => client.buffer.discardedFrames,
      inspectVisuals: (key) => inspectVisuals.current?.(key),
      startProfile: () => {
        recorder.start();
        return client.getStreamMetrics();
      },
      stopProfile: () => recorder.stop(),
      getStreamMetrics: client.getStreamMetrics,
      getSubscriptionMetrics: client.getSubscriptionMetrics,
      setSubscription: (options) => client.setSubscription(options),
      updateViewport: (planes) => client.updateViewport(planes),
    };
    return () => {
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
        <Link to="/" className="brand" title="Choose scene or session">
          <span className="brand-mark">S</span> SPATIAL<span>SYNC</span>
        </Link>
        <div className="workspace-title">
          <strong>{manifest?.sceneName ?? 'Opening session…'}</strong>
          <span>{sessionId}</span>
        </div>
        <div className="header-status">
          <span className={`connection-dot ${connected ? 'connected' : ''}`} />
          <span data-testid="connection-status">{state.connection}</span>
          <span className="source-pill">
            {manifest?.source.kind ?? 'source'}
          </span>
          <Link to="/" className="subtle-link">
            Sessions ↗
          </Link>
          <button
            disabled={pending}
            onClick={async () => {
              try {
                await request<void>(
                  `/sessions/${encodeURIComponent(sessionId)}`,
                  { method: 'DELETE' },
                );
                await navigate({ to: '/' });
              } catch (error) {
                setLoadError((error as Error).message);
              }
            }}
          >
            Close session
          </button>
        </div>
      </header>
      <div className="control-bar">
        <div className="playback-controls">
          <span
            className={`state-pill ${state.sessionState.toLowerCase()}`}
            data-testid="session-state"
          >
            {state.sessionState}
          </span>
          <button
            className="primary"
            disabled={!connected || pending || !active}
            onClick={() => client.command(paused ? 'resume' : 'pause')}
            aria-label={paused ? 'Resume simulation' : 'Pause simulation'}
          >
            <span>{paused ? '▶' : 'Ⅱ'}</span> {paused ? 'Resume' : 'Pause'}
          </button>
          <button
            disabled={!connected || pending || !paused}
            onClick={() => client.command('step', { count: 1 })}
            aria-label="Step simulation"
          >
            ▹│ Step
          </button>
          <button
            disabled={!connected || pending || !active}
            onClick={() => client.command('reset')}
            aria-label="Reset simulation"
          >
            ↺ Reset
          </button>
          <button
            disabled={pending || !active}
            onClick={() => void lifecycle('reload')}
          >
            Reload scene
          </button>
          {state.sessionState === 'FAILED' && (
            <button
              disabled={pending}
              onClick={() => void lifecycle('restart')}
            >
              Restart source
            </button>
          )}
        </div>
        <div className="timing-control">
          <span className="muted">simTime</span>
          <output data-testid="sim-time">
            {state.simTime.toFixed(4)}
            <small>s</small>
          </output>
          <label>
            RTF
            <select
              aria-label="Realtime factor"
              value={
                state.realtimeFactor === null
                  ? 'unlimited'
                  : String(state.realtimeFactor)
              }
              onChange={(event) => {
                client.command('set_realtime_factor', {
                  value:
                    event.target.value === 'unlimited'
                      ? null
                      : Number(event.target.value),
                });
              }}
              disabled={!connected || pending || !active}
            >
              <option value="0.25">0.25×</option>
              <option value="0.5">0.5×</option>
              <option value="1">1.0×</option>
              <option value="2">2.0×</option>
              <option value="unlimited">Unlimited</option>
            </select>
          </label>
        </div>
      </div>
      {manifest && (
        <SubscriptionControls
          client={client}
          manifest={manifest}
          selected={selected}
        />
      )}
      {(loadError || state.error) && (
        <div role="alert" className="banner error">
          {loadError ?? state.error}
        </div>
      )}
      {(pending || state.commandResult) && (
        <div role="status" className="command-notice">
          {pending
            ? 'Waiting for source acknowledgement…'
            : state.commandResult}
        </div>
      )}
      {!manifest ? (
        <div className="loading-workspace">
          <div className="loading-orbit" />
          <h2>
            {loadError
              ? 'Gateway unavailable'
              : 'Loading your spatial workspace'}
          </h2>
          <p>
            {loadError ??
              'Reading the scene manifest and establishing the transform stream…'}
          </p>
          <Link to="/">Back to sessions</Link>
        </div>
      ) : (
        <div className="workbench-main">
          <SceneTree
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
                  aria-selected={tab === 'scene'}
                  onClick={() => setTab('scene')}
                >
                  Spatial view
                </button>
                <button
                  aria-selected={tab === 'diagnostics'}
                  onClick={() => setTab('diagnostics')}
                >
                  Diagnostics
                  {warningCount > 0 && (
                    <span className="warning-count">{warningCount}</span>
                  )}
                </button>
              </div>
              <div className="layer-toggles">
                {(['grid', 'sites', 'origins', 'colliders'] as const).map(
                  (layer) => (
                    <label key={layer}>
                      <input
                        type="checkbox"
                        checked={layers[layer]}
                        onChange={(event) =>
                          setLayers((previous) => ({
                            ...previous,
                            [layer]: event.target.checked,
                          }))
                        }
                      />
                      {layer === 'sites'
                        ? 'Sites'
                        : layer[0].toUpperCase() + layer.slice(1)}
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
                }}
              />
              {tab === 'diagnostics' && (
                <Diagnostics
                  {...{ client, state, metrics: metrics.current, warnings }}
                />
              )}
            </div>
          </section>
          <Inspector {...{ manifest, selected, client, state }} />
        </div>
      )}
      <footer className="status-bar">
        <div>
          <span className="live-dot" />
          {metrics.current.fps.toFixed(0)} <small>RENDER FPS</small>
        </div>
        <div>
          {client.buffer.grantedRateHz} Hz <small>STREAM</small>
        </div>
        <div>
          {state.actualRtf.toFixed(2)}× <small>ACTUAL RTF</small>
        </div>
        <div
          className={
            state.sessionState === 'RUNNING' && age > 1000 ? 'warning-text' : ''
          }
        >
          {state.sessionState === 'RUNNING'
            ? `${age.toFixed(0)} ms`
            : paused
              ? 'Paused'
              : '—'}{' '}
          <small>FRAME AGE</small>
        </div>
        <div>
          Epoch{' '}
          <output data-testid="epoch">
            {client.buffer.epoch < 0
              ? (manifest?.epoch ?? '—')
              : client.buffer.epoch}
          </output>
        </div>
        <div>
          Seq{' '}
          <output data-testid="sequence">
            {client.buffer.lastSeq < 0 ? '—' : client.buffer.lastSeq}
          </output>
        </div>
        <button
          className="status-warnings"
          onClick={() => setTab('diagnostics')}
        >
          {warningCount ? `△ ${warningCount} warnings` : '✓ Scene compatible'}
        </button>
        <span className="revision-label">
          {manifest?.sceneRevision ?? 'Awaiting manifest'}
        </span>
      </footer>
    </main>
  );
}
