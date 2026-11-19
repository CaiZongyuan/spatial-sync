import type { ReactNode } from 'react';
import { getSceneDetail, type SceneVersion } from '../lib/library';
import { thumbnailQuery, useThumbnailTheme } from '../lib/thumbnails';
import { StaticThumbnail } from './StaticThumbnail';
import { useThumbnailQueue } from './ThumbnailQueue';

export function SceneThumbnails({ children }: {
  scenes: SceneVersion[]; children: (thumbnail: (scene: SceneVersion) => ReactNode) => ReactNode;
}) {
  const theme = useThumbnailTheme();
  const { enqueue, tokens, host } = useThumbnailQueue<SceneVersion>({
    theme,
    prepare: async (scene, signal) => {
      const detail = await getSceneDetail(scene.sceneId, String(scene.rev), scene.preview.source, signal);
      if (detail.preview.status !== 'available' && detail.preview.status !== 'stale') return null;
      return { scene: detail, capturedAt: detail.preview.capturedAt };
    },
  });
  return <>
    {children(scene => {
      const key = `${scene.id}:${scene.contentHash}:${theme}:${scene.preview.source}`;
      const path = thumbnailQuery('scene', scene.id, scene.contentHash, theme, scene.preview.source);
      return <StaticThumbnail key={`${scene.id}:${scene.contentHash}:${theme}`}
        url={scene.thumbnailUrl ?? '/api/v1' + path}
        href={`/scenes/${encodeURIComponent(scene.sceneId)}/${encodeURIComponent(scene.id.split('@')[1])}?source=${scene.preview.source}`}
        linkLabel={`打开 ${scene.id}`}
        label={`${scene.id} 的静态预览`}
        declared={Boolean(scene.thumbnailUrl)}
        refreshToken={tokens[key] ?? 0}
        onMissing={() => enqueue({ key, path, item: scene })} />;
    })}
    {host}
  </>;
}
