import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { SceneVersion } from '../lib/library';
import type { SceneManifest } from '../lib/protocol';
import type { ScenePreviewSource } from '../lib/scene/preview-source';
import { useViewerTheme } from '../lib/scene/viewer-theme';
import { FrozenViewport, useScenePreview } from './ScenePreview';

// One bounded in-memory cache and one queue renderer for the list route.
const images = new Map<string, string>();
function ThumbnailRenderer({ scene, captureKey, done }: { scene: SceneVersion; captureKey: string; done: (png?: string) => void }) {
  const { client, state, error } = useScenePreview(scene.preview);
  const [display, setDisplay] = useState<{ client: ScenePreviewSource; manifest: SceneManifest; captureKey: string; capture: (png?: string) => void }>();
  useEffect(() => {
    if (state.connection === 'connected' && state.manifest)
      setDisplay({ client, manifest: state.manifest, captureKey, capture: done });
  }, [client, state.connection, state.manifest, captureKey, done]);
  useEffect(() => { if (error) done(); }, [error, done]);
  // Keep one Canvas while the next source loads; replace only its scene contents.
  return <div className="scene-thumbnail-renderer" aria-hidden="true">{display &&
    <FrozenViewport {...display} />}</div>;
}

export function SceneThumbnails({ scenes, children }: { scenes: SceneVersion[]; children: (thumbnail: (scene: SceneVersion) => ReactNode) => ReactNode }) {
  const theme = useViewerTheme();
  const themeKey = JSON.stringify(theme);
  const keyFor = useCallback((scene: SceneVersion) => [scene.id, scene.contentHash, scene.preview.source, scene.preview.capturedAt, themeKey].join(':'), [themeKey]);
  const [results, setResults] = useState(new Map<string, string | undefined>());
  const available = useMemo(() => scenes.filter(scene => scene.preview.status === 'available'), [scenes]);
  useEffect(() => {
    setResults(previous => {
      const current = new Map<string, string | undefined>();
      for (const scene of available) {
        const key = keyFor(scene);
        if (previous.has(key)) current.set(key, previous.get(key));
        else if (images.has(key)) current.set(key, images.get(key));
      }
      return current;
    });
  }, [available, keyFor]);
  const next = available.find(scene => !images.has(keyFor(scene)) && !results.has(keyFor(scene)));
  const key = next && keyFor(next);
  const activeKey = useRef(key);
  useLayoutEffect(() => {
    activeKey.current = key;
    return () => { activeKey.current = undefined; };
  }, [key]);
  const done = useCallback((png?: string) => {
    if (!key || activeKey.current !== key) return;
    if (png) {
      if (images.size >= 32) images.delete(images.keys().next().value!);
      images.set(key, png);
    }
    setResults(previous => new Map(previous).set(key, png));
  }, [key]);
  return <>{children(scene => {
    const image = results.get(keyFor(scene)) ?? images.get(keyFor(scene));
    return image ? <img src={image} alt={`${scene.id} 的真实初始场景`} /> : <span className="scene-thumbnail-unavailable" title={scene.preview.reason ?? undefined}>
      <span aria-hidden="true">◇</span>{scene.preview.status === 'available' ? results.has(keyFor(scene)) ? '预览加载失败' : '生成预览…' : '暂无静态预览'}</span>;
  })}{next && key && <ThumbnailRenderer scene={next} captureKey={key} done={done} />}</>;
}
