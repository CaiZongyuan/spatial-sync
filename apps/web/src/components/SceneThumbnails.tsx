import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { SceneVersion } from '../lib/library';
import type { SceneManifest } from '../lib/protocol';
import type { ScenePreviewSource } from '../lib/scene/preview-source';
import { useViewerTheme } from '../lib/scene/viewer-theme';
import { FrozenViewport, useScenePreview } from './ScenePreview';
import { ThumbnailQueue } from './ThumbnailQueue';

function ThumbnailRenderer({ scene, captureKey, done }: { scene: SceneVersion; captureKey: string; done: (png?: string) => void }) {
  const { client, state, error } = useScenePreview(scene.preview);
  const [display, setDisplay] = useState<{ client: ScenePreviewSource; manifest: SceneManifest; captureKey: string; capture: (png?: string) => void }>();
  useEffect(() => {
    if (state.connection === 'connected' && state.manifest)
      setDisplay({ client, manifest: state.manifest, captureKey, capture: done });
  }, [client, state.connection, state.manifest, captureKey, done]);
  useEffect(() => { if (error) done(); }, [error, done]);
  return display && <FrozenViewport {...display} />;
}

export function SceneThumbnails({ scenes, children }: { scenes: SceneVersion[]; children: (thumbnail: (scene: SceneVersion) => ReactNode) => ReactNode }) {
  const theme = useViewerTheme();
  const themeKey = JSON.stringify(theme);
  const keyFor = useCallback((scene: SceneVersion) => ['scene', scene.id, scene.contentHash, scene.preview.source, scene.preview.capturedAt, themeKey].join(':'), [themeKey]);
  const entries = useMemo(() => scenes.map(scene => ({ key: keyFor(scene), label: `${scene.id} 的真实初始场景`, reason: scene.preview.reason,
    render: scene.preview.status === 'available' ? (done: (png?: string) => void) => <ThumbnailRenderer scene={scene} captureKey={keyFor(scene)} done={done} /> : undefined,
  })), [scenes, keyFor]);
  return <ThumbnailQueue entries={entries}>{thumbnail => children(scene => thumbnail(keyFor(scene)))}</ThumbnailQueue>;
}
