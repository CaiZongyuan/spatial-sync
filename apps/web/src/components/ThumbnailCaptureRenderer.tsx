import { useEffect, useState } from 'react';
import type { AssetVersion } from '../lib/asset-library';
import type { SceneVersion } from '../lib/library';
import type { SceneManifest } from '../lib/protocol';
import type { ScenePreviewSource } from '../lib/scene/preview-source';
import { AssetModelViewport } from './AssetModelViewport';
import { FrozenViewport, useScenePreview } from './ScenePreview';

function SceneCapture({ scene, done }: { scene: SceneVersion; done: (png?: string) => void }) {
  const { client, state, error } = useScenePreview(scene.preview);
  const [display, setDisplay] = useState<{ client: ScenePreviewSource; manifest: SceneManifest }>();
  useEffect(() => {
    if (state.connection === 'connected' && state.manifest) setDisplay({ client, manifest: state.manifest });
  }, [client, state.connection, state.manifest]);
  useEffect(() => { if (error) done(); }, [error, done]);
  return display && <FrozenViewport {...display} capture={done} captureKey={scene.contentHash} />;
}

export default function ThumbnailCaptureRenderer({ scene, asset, done }: {
  scene?: SceneVersion; asset?: AssetVersion; done: (png?: string) => void;
}) {
  return scene ? <SceneCapture scene={scene} done={done} /> : asset?.visualUrl ?
    <AssetModelViewport url={asset.visualUrl} capture={done} captureKey={asset.contentHash} /> : null;
}
