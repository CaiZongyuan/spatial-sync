import type { ReactNode } from 'react';
import type { SceneVersion } from '../lib/library';
import { thumbnailQuery, useThumbnailTheme } from '../lib/thumbnails';
import { StaticThumbnail } from './StaticThumbnail';

export function SceneThumbnails({ children }: {
  scenes: SceneVersion[]; children: (thumbnail: (scene: SceneVersion) => ReactNode) => ReactNode;
}) {
  const theme = useThumbnailTheme();
  return children(scene => <StaticThumbnail key={`${scene.id}:${scene.contentHash}:${theme}`}
    url={scene.thumbnailUrl ?? '/api/v1' + thumbnailQuery('scene', scene.id, scene.contentHash, theme, scene.preview.source)}
    label={`${scene.id} 的静态预览`} declared={Boolean(scene.thumbnailUrl)} />);
}
