import { useEffect, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { Line, OrbitControls } from '@react-three/drei';
import { request } from '../lib/api';
import { useViewerTheme } from '../lib/scene/viewer-theme';

type Point = [number, number, number];
interface ReferenceMotion { id: string; sha256: string; asset: string; assetContentHash: string }
interface Ghost { links: [number, number][]; samples: { time: number; positions: Point[] }[] }
interface Preview { asset: string; assetContentHash: string; loop: boolean; ghost: Ghost }

export function ReferenceMotionPreview({ asset, contentHash }: { asset: string; contentHash: string }) {
  const theme = useViewerTheme();
  const [references, setReferences] = useState<ReferenceMotion[]>();
  const [digest, setDigest] = useState('');
  const [preview, setPreview] = useState<Preview>();
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string>();
  const duration = preview?.ghost.samples.at(-1)?.time ?? 0;
  useEffect(() => {
    const controller = new AbortController();
    request<{ referenceMotions: ReferenceMotion[] }>('/library/reference-motions', { signal: controller.signal })
      .then(value => setReferences(value.referenceMotions.filter(row => row.asset === asset && row.assetContentHash === contentHash)))
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [asset, contentHash]);
  useEffect(() => {
    setPreview(undefined); setPlaying(false); setTime(0); setError(undefined);
    if (!digest) return;
    const controller = new AbortController();
    request<Preview>(`/library/reference-motions/${digest}/1/preview`, { signal: controller.signal })
      .then(value => {
        if (value.asset !== asset || value.assetContentHash !== contentHash) throw new Error('参考动作绑定了另一素材版本');
        if (!controller.signal.aborted) setPreview(value);
      })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [digest, asset, contentHash]);
  useEffect(() => {
    if (!playing || !duration) return;
    let frame: number;
    let start: number | undefined;
    const tick = (now: number) => {
      start ??= now - time * 1000;
      const elapsed = (now - start) / 1000;
      setTime(preview?.loop ? elapsed % duration : Math.min(duration, elapsed));
      if (elapsed < duration || preview?.loop) frame = requestAnimationFrame(tick);
      else setPlaying(false);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // The playback effect owns the initial time until paused or scrubbed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, preview, duration]);

  if (!references?.length && !error) return null;
  const samples = preview?.ghost.samples;
  const next = samples?.findIndex(row => row.time > time) ?? -1;
  const sample = next < 0 ? samples?.at(-1) : samples?.[Math.max(0, next - 1)];
  const origin = samples?.[0]?.positions[0];
  const radius = samples?.[0]?.positions.reduce((bound, point) => Math.max(bound, ...point.map(Math.abs)), .1) ?? .1;
  return <section className="asset-panel">
    <header><h2>参考动作</h2></header>
    <div className="asset-panel-body">
      <label>固定版本 <select aria-label="参考动作版本" value={digest} onChange={event => setDigest(event.target.value)}>
        <option value="">选择参考动作</option>
        {references?.map(row => <option value={row.sha256} key={row.id}>{row.id}</option>)}
      </select></label>
      {error && <p role="alert">{error}</p>}
      {digest && !preview && !error && <p role="status">正在读取参考动作…</p>}
      {preview && sample && origin && <>
        <div className="asset-detail-model" aria-label="参考动作三维运动学预览">
          <Canvas camera={{ position: [radius * 2.4, radius * 1.8, radius * 2.2], near: radius / 1000 }}>
            <gridHelper args={[radius * 4, 12]} />
            <group rotation={[-Math.PI / 2, 0, 0]}>
              {preview.ghost.links.map(([a, b]) => <Line key={b} points={[sample.positions[a], sample.positions[b]]}
                color={theme.accent} lineWidth={3} transparent opacity={0.7} />)}
              {sample.positions.map((point, index) => <mesh key={index} position={point}>
                <sphereGeometry args={[radius / 45, 8, 6]} /><meshBasicMaterial color={theme.accent} transparent opacity={0.75} />
              </mesh>)}
            </group>
            <OrbitControls target={[0, origin[2], 0]} />
          </Canvas>
        </div>
        <button type="button" onClick={() => { if (!playing && time >= duration) setTime(0); setPlaying(!playing); }}>
          {playing ? '暂停' : '播放'}
        </button>
        <label> 相位时间 <input aria-label="相位时间" type="range" min={0} max={duration} step={0.01} value={time}
          onChange={event => { setPlaying(false); setTime(Number(event.target.value)); }} /></label>
        <output>{time.toFixed(2)} / {duration.toFixed(2)} s</output>
      </>}
    </div>
  </section>;
}
