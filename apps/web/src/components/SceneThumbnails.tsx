import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { SceneVersion } from '../lib/library';
import { useViewerTheme } from '../lib/scene/viewer-theme';
import { FrozenViewport, useScenePreview } from './ScenePreview';

// One bounded in-memory cache and one queue renderer for the list route.
const images = new Map<string, string>();
function ThumbnailRenderer({ scene, done }: { scene: SceneVersion; done: (png?: string) => void }) {
  const { client, state, error } = useScenePreview(scene.preview);
  useEffect(() => { if (error) done(); }, [error, done]);
  return <div className="scene-thumbnail-renderer" aria-hidden="true">{state.manifest &&
    <FrozenViewport client={client} manifest={state.manifest} capture={done} />}</div>;
}

export function SceneThumbnails({ scenes, children }: { scenes: SceneVersion[]; children: (thumbnail: (scene: SceneVersion) => ReactNode) => ReactNode }) {
  const theme = useViewerTheme();
  const themeKey = JSON.stringify(theme);
  const keyFor = useCallback((scene: SceneVersion) => [scene.id, scene.contentHash, scene.preview.source, scene.preview.capturedAt, themeKey].join(':'), [themeKey]);
  const [finished, setFinished] = useState(new Set<string>());
  const available = useMemo(() => scenes.filter(scene => scene.preview.status === 'available'), [scenes]);
  const next = available.find(scene => !images.has(keyFor(scene)) && !finished.has(keyFor(scene)));
  const key = next && keyFor(next);
  const done = useCallback((png?: string) => {
    if (!key) return;
    if (png) {
      if (images.size >= 32) images.delete(images.keys().next().value!);
      images.set(key, png);
    }
    setFinished(previous => new Set(previous).add(key));
  }, [key]);
  return <>{children(scene => {
    const image = images.get(keyFor(scene));
    return image ? <img src={image} alt={`${scene.id} 的真实初始场景`} /> : <span className="scene-thumbnail-unavailable" title={scene.preview.reason ?? undefined}>
      <span aria-hidden="true">◇</span>{scene.preview.status === 'available' ? finished.has(keyFor(scene)) ? '预览加载失败' : '生成预览…' : '暂无静态预览'}</span>;
  })}{next && <ThumbnailRenderer key={key} scene={next} done={done} />}</>;
}
