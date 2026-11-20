import { useContext, useEffect, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { Line, OrbitControls } from '@react-three/drei';
import { request } from '../lib/api';
import { ApprovalInboxContext } from '../lib/approvals';
import { motionVersionPath, motionWrite, referenceMatches,
  type GhostPreview, type MotionPin, type ReferenceDetail, type ReferencePin } from '../lib/motion';
import { ghostPositionsAt, phaseTime } from '../lib/motion-phase';
import { useViewerTheme } from '../lib/scene/viewer-theme';
import { MotionVideoWorkflow } from './MotionVideoWorkflow';

export function ReferenceMotionPreview({ asset, contentHash }: { asset: string; contentHash: string }) {
  const permissions = useContext(ApprovalInboxContext);
  const operator = permissions.inbox?.role === 'operator' && !permissions.error;
  const [references, setReferences] = useState<ReferencePin[]>([]);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState<string>();
  const pin = references.find(row => row.id === selected);
  useEffect(() => {
    const controller = new AbortController();
    void request<{ referenceMotions: ReferencePin[] }>('/library/reference-motions', { signal: controller.signal })
      .then(value => {
        if (controller.signal.aborted) return;
        const matching = value.referenceMotions.filter(row => row.asset === asset && row.assetContentHash === contentHash);
        setReferences(current => [...new Map([...matching, ...current].map(row => [row.id, row])).values()]);
      }).catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [asset, contentHash]);

  if (!operator && !references.length && !error) return null;
  return <section className="asset-panel">
    <header><h2>参考动作</h2></header>
    <div className="asset-panel-body motion-workbench">
      {operator && <MotionVideoWorkflow asset={asset} contentHash={contentHash} onReference={reference => {
        setReferences(current => [...current.filter(row => row.id !== reference.id), reference]);
        setSelected(reference.id);
      }} />}
      {references.length > 0 && <label>参考动作版本 <select value={selected} onChange={event => setSelected(event.target.value)}>
        <option value="">选择参考动作</option>
        {references.map(row => <option value={row.id} key={row.id}>{row.id}</option>)}
      </select></label>}
      {error && <p role="alert">{error}</p>}
      {pin && <ReferencePlayer key={pin.id} pin={pin} operator={operator} />}
    </div>
  </section>;
}

function ReferencePlayer({ pin, operator }: { pin: ReferencePin; operator: boolean }) {
  const theme = useViewerTheme();
  const [loaded, setLoaded] = useState<{ detail: ReferenceDetail; preview: GhostPreview }>();
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string>();
  const duration = loaded?.preview.ghost.samples.at(-1)?.time ?? 0;

  useEffect(() => {
    const controller = new AbortController();
    async function read() {
      const path = motionVersionPath(pin.id, 'reference-motion');
      const [detail, preview] = await Promise.all([
        request<ReferenceDetail>(path, { signal: controller.signal }),
        request<GhostPreview>(path + '/preview', { signal: controller.signal }),
      ]);
      if (!referenceMatches(pin, detail, preview)) throw new Error('参考动作与固定版本或素材哈希不符。');
      if (!controller.signal.aborted) setLoaded({ detail, preview });
    }
    void read().catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [pin]);

  useEffect(() => {
    if (!playing || !duration || !loaded) return;
    let frame: number;
    let start: number | undefined;
    const { loop } = loaded.preview;
    const tick = (now: number) => {
      start ??= now - time * 1000;
      const elapsed = (now - start) / 1000;
      setTime(phaseTime(elapsed, duration, loop));
      if (elapsed < duration || loop) frame = requestAnimationFrame(tick);
      else setPlaying(false);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // Play starts at the current cursor. Scrubbing pauses playback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, loaded, duration]);

  if (error) return <p role="alert">{error}</p>;
  if (!loaded) return <p role="status">正在读取参考动作…</p>;
  const { detail, preview } = loaded;
  const first = preview.ghost.samples[0]?.positions;
  if (!first?.length) return <p role="alert">此版本没有运动学帧。</p>;
  const positions = ghostPositionsAt(preview.ghost.samples, time);
  const radius = first.reduce((bound, point) => Math.max(bound, ...point.map(Math.abs)), .1);
  const rights = detail.source.video?.license ?? detail.source.keyframes?.license;

  return <>
    <dl className="asset-kv motion-provenance">
      <div><dt>目标素材</dt><dd><code>{pin.asset}</code> · <code title={pin.assetContentHash}>{pin.assetContentHash}</code></dd></div>
      <div><dt>参考动作</dt><dd><code>{pin.id}</code></dd></div>
      {detail.source.clip && <div><dt>源片段</dt><dd><code>{detail.source.clip.id}</code></dd></div>}
      {detail.source.video && <div><dt>源视频</dt><dd><code>{detail.source.video.id}</code>
        <code title={detail.source.video.sha256}>{detail.source.video.sha256}</code></dd></div>}
      {rights && <div><dt>来源与许可</dt><dd>{rights.attribution} · {rights.license}
        {rights.sourceUrl && <small>{rights.sourceUrl}</small>}</dd></div>}
      {detail.source.model && <div><dt>姿态模型</dt><dd>{detail.source.model.runtime} · {detail.source.model.landmarkLicense}
        <code title={detail.source.model.sha256}>{detail.source.model.sha256}</code>
        <small>检测器许可 {detail.source.model.detectorLicense ?? '未记录'}</small></dd></div>}
      <div><dt>可行性</dt><dd>足滑 {detail.report.maxFootSlipM ?? '—'} m · 支撑裕量 {detail.report.minSupportMarginM ?? '—'} m ·
        关节限位饱和 {detail.report.jointLimitSaturation ?? '—'}</dd></div>
    </dl>
    <div className="asset-detail-model" aria-label="参考动作三维运动学预览">
      <Canvas camera={{ position: [radius * 2.4, radius * 1.8, radius * 2.2], near: radius / 1000 }}>
        <gridHelper args={[radius * 4, 12]} />
        <group rotation={[-Math.PI / 2, 0, 0]}>
          {preview.ghost.links.map(([a, b]) => <Line key={b} points={[positions[a], positions[b]]}
            color={theme.accent} lineWidth={3} transparent opacity={0.7} />)}
          {positions.map((point, index) => <mesh key={index} position={point}>
            <sphereGeometry args={[radius / 45, 8, 6]} /><meshBasicMaterial color={theme.accent} transparent opacity={0.75} />
          </mesh>)}
        </group>
        <OrbitControls target={[0, first[0][2], 0]} />
      </Canvas>
    </div>
    <div className="motion-playback">
      <button type="button" onClick={() => { if (!playing && time >= duration) setTime(0); setPlaying(!playing); }}>
        {playing ? '暂停' : '播放'}
      </button>
      <label>相位时间 <input aria-label="相位时间" type="range" min={0} max={duration} step="any" value={time}
        onChange={event => { setPlaying(false); setTime(Number(event.target.value)); }} /></label>
      <output>{time.toFixed(2)} / {duration.toFixed(2)} s</output>
    </div>
    {operator && <ReferenceTaskForm pin={{ id: pin.id, sha256: pin.sha256 }} />}
  </>;
}

function ReferenceTaskForm({ pin }: { pin: MotionPin }) {
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<{ id: string; contentHash: string }>();
  const [error, setError] = useState<string>();
  return <details className="motion-workflow"><summary>从参考动作创建 Task</summary>
    <form className="motion-form" onSubmit={async event => {
      event.preventDefault();
      if (!confirmed || busy) return;
      const file = new FormData(event.currentTarget).get('task');
      if (!(file instanceof File) || !file.size || file.size > 1024 * 1024) {
        setError('选择不超过 1 MiB 的 Task JSON。'); return;
      }
      setBusy(true); setError(undefined);
      try {
        const { task, observation } = JSON.parse(await file.text());
        setReceipt(await motionWrite('/tasks/reference-motion', { reference: pin, task, observation }));
      } catch (failure) { setError((failure as Error).message); }
      finally { setBusy(false); }
    }}>
      <label><input type="checkbox" checked={confirmed} disabled={busy}
        onChange={event => setConfirmed(event.target.checked)} />已检查此版本的动作预览</label>
      <label>Task 和观测 JSON<input type="file" name="task" accept=".json,application/json" required disabled={busy} /></label>
      <button type="submit" disabled={!confirmed || busy}>创建固定版本 Task</button>
      {receipt && <p role="status">Task <code>{receipt.id}</code> · <code>{receipt.contentHash}</code></p>}
      {error && <p role="alert">{error}</p>}
    </form>
  </details>;
}
