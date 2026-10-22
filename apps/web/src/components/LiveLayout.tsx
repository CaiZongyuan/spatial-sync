import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Catalog } from '../lib/protocol';
import type { LiveClient, LiveStatus } from '../lib/live-client';
import { getSceneDetail, type SceneDetail, type ScenePreview } from '../lib/library';
import { reading, type AlertEpisode } from '../lib/live';
import { FrameRecorder } from '../lib/performance/frame-recorder';
import { useScenePreview } from './ScenePreview';
import { useDeviceReadouts } from './DeviceTelemetry';
import { SceneViewport, type InspectVisuals, type RenderedPose, type RenderMetrics } from './viewer/SceneViewport';

const unavailable: ScenePreview = { status: 'unavailable', source: 'synthetic', reason: '未配置布局',
  entityCount: null, bindingCount: null };

export function LiveLayout({ client, catalog, status, selected, readouts, active, select, toggleReadouts }: {
  client: LiveClient; catalog?: Catalog; status?: LiveStatus; selected: string; readouts: boolean;
  active: AlertEpisode[]; select: (key: string) => void; toggleReadouts: () => void;
}) {
  const [layout, setLayout] = useState<SceneDetail>(), [error, setError] = useState<string>();
  useEffect(() => {
    const controller = new AbortController();
    setLayout(undefined); setError(undefined);
    if (!status?.layout) return;
    if (status.layoutError) { setError(status.layoutError); return; }
    const [name, revision] = status.layout.split('@');
    void getSceneDetail(name, revision, undefined, controller.signal).then(value => {
      if (!status.layoutContentHash || value.contentHash !== status.layoutContentHash)
        throw new Error('布局版本已改变。');
      setLayout(value);
    }).catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [status?.layout, status?.layoutContentHash, status?.layoutError]);
  const preview = useScenePreview(layout?.preview ?? unavailable);
  const manifest = preview.state.manifest;
  const bindings = Object.values(catalog?.entities ?? {});
  const values = useDeviceReadouts(client, bindings.map(binding => binding.key));
  const mapped = useMemo(() => new Map(bindings.map(binding => {
    const placement = status?.placements[binding.key];
    const site = manifest?.sites.find(site => site.entityKey === placement);
    const anchor = site?.parentBodyKey ?? placement;
    return [binding.key, anchor && manifest ? manifest.nodes.filter(node => node.kind !== 'group'
      && (node.entityKey === anchor || node.entityKey.startsWith(anchor + '/'))).map(node => node.entityKey) : []];
  })), [catalog, status?.placements, manifest]);
  const annotations = bindings.map((binding, index) => {
    const alert = active.find(alert => alert.entityKey === binding.key), entity = values[index];
    return { key: binding.key, label: binding.key.split('/').at(-1)!, bodyKeys: mapped.get(binding.key) ?? [],
      detail: readouts ? Object.entries(entity?.values ?? {}).filter(([, value]) => typeof value === 'number')
        .slice(0, 2).map(([name, value]) => `${reading(value)} ${catalog?.types[binding.type].properties[name]?.unit ?? name}`).join(' · ') : '',
      stale: Object.values(entity?.meta ?? {}).some(meta => meta.stale),
      alert: alert ? (alert.acknowledgement ? 'acknowledged' : 'raised') as 'acknowledged' | 'raised' : undefined };
  });
  const metrics = useRef<RenderMetrics>({ fps: 0, drawCalls: 0, objects: 0, applyMs: 0 });
  const rendered = useRef(new Map<string, RenderedPose>()), inspectVisuals = useRef<InspectVisuals | undefined>(undefined);
  const recorder = useMemo(() => new FrameRecorder(), []), hidden = useMemo(() => new Set<string>(), []);
  const warn = useCallback(() => {}, []);
  const highlightedKeys = useMemo(() => new Set(mapped.get(selected) ?? []), [mapped, selected]);
  const pick = (key: string) => select(bindings.find(binding => mapped.get(binding.key)?.includes(key))?.key ?? '');
  const missing = bindings.filter(binding => !mapped.get(binding.key)?.length).length;
  return <figure className="overview-figure"><div className="overview-stage">
    {manifest ? <SceneViewport client={preview.client} {...{ manifest, selected, hidden, warn, metrics, rendered,
      inspectVisuals, recorder, annotations, highlightedKeys }} select={pick}
      layers={{ colliders: false, sites: false, origins: false, grid: true }} cameraPreset="isometric"
      compact renderContinuously={false} /> : <div className="live-empty live-layout-empty">
      <span className="empty-scene-mark" aria-hidden="true">◇</span><h2>{status?.layout ? '布局预览暂不可用' : '实验室设备'}</h2>
      <p>{status?.layout ? error || preview.error || layout?.preview.reason ? '设备读数与操作仍可在卡片中使用。' :
        '正在读取固定版本的布局…' : '选择右侧设备查看读数与操作。实况独立于仿真会话运行。'}</p>
      <div className="device-legend">{bindings.map(binding => <button key={binding.key} onClick={() => select(binding.key)}>
        <i className={active.some(alert => alert.entityKey === binding.key) ? 'has-alert' : ''} />
        <span>{binding.key.split('/').at(-1)}</span><code>{binding.type}</code></button>)}</div>
    </div>}
  </div><figcaption><strong>图 1</strong><span>{status?.layout ?? '设备列表'} · 红点 = 活动告警
    {manifest && missing > 0 ? ` · ${missing} 台设备仅在卡片中显示` : ''}</span>
    <button className="readout-switch" aria-label="读数" aria-pressed={readouts} onClick={toggleReadouts}><i />读数</button>
    <span className="reference-tag">实况布局</span></figcaption></figure>;
}
