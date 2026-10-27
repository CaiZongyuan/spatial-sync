import { useCallback, useMemo, useRef, useState } from 'react';
import type { SceneManifest } from '../lib/protocol';
import type { ScenePreviewSource } from '../lib/scene/preview-source';
import { deviceKey } from '../lib/info/binding';
import { FrameRecorder } from '../lib/performance/frame-recorder';
import { SceneViewport, type InspectVisuals, type RenderedPose, type RenderMetrics } from './viewer/SceneViewport';

export { useScenePreview } from '../lib/scene/use-preview';

export function FrozenViewport({ client, manifest, capture, captureKey, readouts = false }: {
  client: ScenePreviewSource; manifest: SceneManifest; capture?: (png: string) => void; captureKey?: string; readouts?: boolean;
}) {
  const [selected, select] = useState('');
  const metrics = useRef<RenderMetrics>({ fps: 0, drawCalls: 0, objects: 0, applyMs: 0 });
  const rendered = useRef(new Map<string, RenderedPose>());
  const inspectVisuals = useRef<InspectVisuals | undefined>(undefined);
  const recorder = useMemo(() => new FrameRecorder(), []);
  const hidden = useMemo(() => new Set(manifest.nodes.filter(node => node.kind !== 'group'
    && !node.visuals.length && !node.fallbackVisuals?.length && !node.colliders.length).map(node => node.entityKey)), [manifest]);
  const warn = useCallback(() => {}, []);
  const catalog = client.info.getMetadata().catalog;
  const annotations = !capture && catalog ? Object.values(catalog.entities)
    .filter(binding => Object.keys(catalog.types[binding.type]?.actions ?? {}).length > 0
      || Object.values(catalog.types[binding.type]?.properties ?? {}).some(property => property.staleAfterSeconds !== undefined))
    .map(binding => ({
      key: binding.key,
      label: manifest.nodes.find(node => node.entityKey === binding.key)?.name ?? binding.key.split('/').at(-1) ?? binding.key,
      detail: readouts ? Object.entries(client.info.getEntity(binding.key)?.values ?? {})
        .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value)).slice(0, 3)
        .map(([name, value]) => `${name} ${String(value)}${catalog.types[binding.type]?.properties[name]?.unit ? ` ${catalog.types[binding.type].properties[name].unit}` : ''}`).join(' · ') : '',
      bodyKeys: manifest.nodes.filter(node => node.kind !== 'group' && deviceKey(manifest, catalog, node.entityKey) === binding.key).map(node => node.entityKey),
    })) : undefined;
  return <SceneViewport {...{ client, manifest, selected, select, hidden, warn, metrics, rendered, inspectVisuals, recorder, capture, captureKey, annotations }}
    layers={{ colliders: false, sites: false, origins: false, grid: true }} cameraPreset="isometric" compact renderContinuously={false} />;
}
