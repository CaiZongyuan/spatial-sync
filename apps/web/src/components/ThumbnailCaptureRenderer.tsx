import { useEffect } from 'react';
import type { AssetVersion } from '../lib/asset-library';
import type { SceneVersion } from '../lib/library';
import { AssetModelViewport } from './AssetModelViewport';
import { FrozenViewport, useScenePreview } from './ScenePreview';

function SceneCapture({ scene, done }: { scene: SceneVersion; done: (png?: string) => void }) {
  const { client, state, error } = useScenePreview(scene.preview);
  useEffect(() => { if (error) done(); }, [error, done]);
  return state.connection === 'connected' && state.manifest &&
    <FrozenViewport client={client} manifest={state.manifest} capture={done} captureKey={scene.contentHash} />;
}

export default function ThumbnailCaptureRenderer({ scene, asset, done }: {
  scene?: SceneVersion; asset?: AssetVersion; done: (png?: string) => void;
}) {
  return scene ? <SceneCapture scene={scene} done={done} /> : asset?.visualUrl ?
    <AssetModelViewport url={asset.visualUrl} capture={done} captureKey={asset.contentHash} /> : null;
}
