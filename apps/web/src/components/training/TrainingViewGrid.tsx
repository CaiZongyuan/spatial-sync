import { Canvas } from '@react-three/fiber';
import { View } from '@react-three/drei';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { FrameRecorder } from '../../lib/performance/frame-recorder';
import { ViewerThemeContext, useViewerTheme } from '../../lib/scene/viewer-theme';
import { TrainingViewClient, type TrainingEnvSource, type TrainingViewStatus } from '../../lib/training/view-source';
import { SceneContents, type InspectVisuals, type RenderedPose, type RenderMetrics } from '../viewer/SceneViewport';
import { useReducedMotion } from '../viewer/MotionPreviewLayer';
import './training-view.css';

interface EnvironmentReceipt {
  selected: string;
  epoch: number;
  seq: number;
  rendered: Record<string, RenderedPose>;
}
declare global {
  interface Window {
    __trainingViewDebug?: { jobId: string; read: () => { viewId?: string; state: string; environments: Record<number, EnvironmentReceipt> } };
  }
}
const phases = { waiting: '等待采样', live: '正在接收', stale: '最近采样 · 已暂停更新', ended: '训练已结束', error: '视图不可用' };
const layers = { colliders: false, sites: false, origins: false, grid: true };
const hidden = new Set<string>();

function TrainingEnvironment({ source, index, register }: { source: TrainingEnvSource; index: number;
  register: (envIndex: number, read?: () => EnvironmentReceipt) => void }) {
  const snapshot = useSyncExternalStore(source.subscribe, source.getSnapshot);
  const manifest = snapshot.manifest!;
  const [selected, setSelected] = useState('');
  const [fitRequest, setFitRequest] = useState(0);
  const [fitted, setFitted] = useState(false);
  const [warning, setWarning] = useState<string>();
  const [visible, setVisible] = useState(true);
  const element = useRef<HTMLDivElement>(null), axes = useRef<SVGSVGElement>(null);
  const metrics = useRef<RenderMetrics>({ fps: 0, drawCalls: 0, objects: 0, applyMs: 0 });
  const rendered = useRef(new Map<string, RenderedPose>());
  const inspectVisuals = useRef<InspectVisuals | undefined>(undefined);
  const recorder = useMemo(() => new FrameRecorder(), []);
  const theme = useViewerTheme(), reducedMotion = useReducedMotion();
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    if (element.current) observer.observe(element.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    register(source.envIndex, () => ({ selected, epoch: source.buffer.epoch, seq: source.buffer.lastSeq,
      rendered: Object.fromEntries([...rendered.current].map(([key, pose]) => [key, { p: [...pose.p], q: [...pose.q] }])) }));
    return () => register(source.envIndex);
  }, [source, selected, register]);
  return <section className="training-environment" data-testid={`training-env-${source.envIndex}`} data-selected-key={selected}
    data-camera-fitted={fitted} aria-label={`训练环境 ${source.envIndex}`}>
    <header><strong>环境 {source.envIndex}</strong><span>{snapshot.connection === 'connected' ? '真实采样' : source.hasSnapshot ? '最近采样' : '等待完整采样'}</span>
      <button type="button" disabled={!source.hasSnapshot} aria-label={`重新取景环境 ${source.envIndex}`}
        onClick={() => setFitRequest(value => value + 1)}>重新取景</button></header>
    <div className="training-environment-stage" ref={element}>
      <View className="training-environment-view" index={index + 1} visible={visible && source.hasSnapshot}>
        {source.hasSnapshot && <ViewerThemeContext.Provider value={theme}>
          <color attach="background" args={[theme.ground]} />
          <SceneContents {...{ manifest, selected, metrics, rendered, inspectVisuals, recorder, layers, hidden, fitRequest, reducedMotion }}
            client={source} select={setSelected} warn={setWarning} axesElement={axes} compact diagnostics={false}
            onFramed={() => setFitted(true)} />
        </ViewerThemeContext.Provider>}
      </View>
      {!source.hasSnapshot && <p className="training-environment-placeholder" role="status">等待实际环境的完整位姿…</p>}
    </div>
    <footer><label>查看实体 <select aria-label={`环境 ${source.envIndex} 实体`} value={selected} onChange={event => setSelected(event.target.value)}>
      <option value="">未选择</option>
      {manifest.nodes.filter(node => node.kind !== 'group').map(node => <option key={node.entityKey} value={node.entityKey}>{node.name}</option>)}
      {manifest.sites.map(site => <option key={site.entityKey} value={site.entityKey}>{site.entityKey}</option>)}
    </select></label><span>只读 · 米 · Z 向上</span></footer>
    {warning && <p className="training-environment-warning" role="status">{warning}</p>}
  </section>;
}

export function TrainingViewGrid({ jobId, onStatusChange }: { jobId: string; onStatusChange?: (status: TrainingViewStatus) => void }) {
  const client = useMemo(() => new TrainingViewClient(jobId), [jobId]);
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const container = useRef<HTMLDivElement>(null);
  const readers = useMemo(() => new Map<number, () => EnvironmentReceipt>(), [client]);
  const register = useMemo(() => (env: number, read?: () => EnvironmentReceipt) => {
    if (read) readers.set(env, read); else readers.delete(env);
  }, [readers]);
  const statusCallback = useRef(onStatusChange);
  statusCallback.current = onStatusChange;
  const [visible, setVisible] = useState(true), [foreground, setForeground] = useState(document.visibilityState !== 'hidden');
  useEffect(() => { client.start(); return () => client.stop(); }, [client]);
  useEffect(() => {
    statusCallback.current?.({ state: state.state, hello: state.hello, error: state.error, message: state.message });
  }, [state.state, state.hello, state.error, state.message]);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    if (container.current) observer.observe(container.current);
    const changed = () => setForeground(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', changed);
    return () => { observer.disconnect(); document.removeEventListener('visibilitychange', changed); };
  }, []);
  useEffect(() => {
    const debug = { jobId, read: () => ({ viewId: client.getSnapshot().viewId, state: client.getSnapshot().state,
      environments: Object.fromEntries([...readers].map(([env, read]) => [env, read()])) }) };
    window.__trainingViewDebug = debug;
    return () => { if (window.__trainingViewDebug === debug) delete window.__trainingViewDebug; };
  }, [jobId, client, readers]);
  const environments = state.state === 'ended' || state.state === 'error' ? [] : state.hello?.environments ?? [];
  return <section className="training-view" aria-label="训练只读视图" data-testid="training-view-grid" data-state={state.state}>
    <header className="training-view-heading"><h2>训练视图 <small>只读</small></h2><span role="status">{phases[state.state]}</span></header>
    {state.error && <p role="alert">{state.error}</p>}
    {state.message && <p className="training-view-message">{state.message}</p>}
    <div className="training-view-content" ref={container}>
      <div className={`training-view-environments${environments.length === 1 ? ' is-single' : ''}`}>
        {environments.map((env, index) => {
          const source = state.sources.get(env);
          return source ? <TrainingEnvironment key={`${state.viewId}:${env}`} {...{ source, index, register }} />
            : <div className="training-environment-placeholder" key={env}>环境 {env} · 等待空间描述…</div>;
        })}
      </div>
      {state.sources.size > 0 && <Canvas className="training-view-canvas" flat shadows eventSource={container} frameloop={visible && foreground ? 'always' : 'never'}
        style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} dpr={[1, 1.5]}
        gl={{ antialias: true, alpha: true }} camera={{ position: [4.2, 3, 4.8], fov: 44, near: .01, far: 100 }}>
        <View.Port />
      </Canvas>}
      {!environments.length && state.state === 'waiting' && <p className="training-view-message">等待节点开始发布训练环境。收到完整实际采样后显示视图。</p>}
    </div>
  </section>;
}
