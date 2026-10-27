import type { ReactNode } from 'react';
import type { AssetVersion } from '../lib/asset-library';
import { thumbnailQuery, useThumbnailTheme } from '../lib/thumbnails';
import { StaticThumbnail } from './StaticThumbnail';

export function AssetThumbnails({ children }: {
  assets: AssetVersion[]; children: (thumbnail: (asset: AssetVersion) => ReactNode) => ReactNode;
}) {
  const theme = useThumbnailTheme();
  return children(asset => <StaticThumbnail key={`${asset.id}:${asset.contentHash}:${theme}`}
    url={asset.thumbnailUrl ?? '/api/v1' + thumbnailQuery('asset', asset.id, asset.contentHash, theme)}
    label={`${asset.id} 的静态预览`} declared={Boolean(asset.thumbnailUrl)} />);
}
