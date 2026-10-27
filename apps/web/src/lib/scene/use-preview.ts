import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import type { ScenePreview } from '../library';
import { ScenePreviewSource } from './preview-source';

export function useScenePreview(preview: ScenePreview) {
  const client = useMemo(() => new ScenePreviewSource(preview), [preview]);
  const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
  const metadata = useSyncExternalStore(client.info.subscribeMetadata, client.info.getMetadata);
  const [failure, setFailure] = useState<{ client: ScenePreviewSource; message: string }>();
  useEffect(() => {
    let alive = true;
    setFailure(undefined);
    if (preview.status === 'available') void client.start().catch((error: Error) => {
      if (alive) setFailure({ client, message: error.message });
    });
    return () => { alive = false; client.stop(); };
  }, [client, preview.status]);
  return { client, state, catalog: metadata.catalog, error: failure?.client === client ? failure.message : undefined };
}
