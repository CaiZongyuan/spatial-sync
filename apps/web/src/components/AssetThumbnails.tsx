import type { ReactNode } from 'react';
import { getAssetDetail, type AssetVersion } from '../lib/asset-library';
import { thumbnailQuery, useThumbnailTheme } from '../lib/thumbnails';
import { StaticThumbnail } from './StaticThumbnail';
import { useThumbnailQueue } from './ThumbnailQueue';

export function AssetThumbnails({ children }: {
  assets: AssetVersion[]; children: (thumbnail: (asset: AssetVersion) => ReactNode) => ReactNode;
}) {
  const theme = useThumbnailTheme();
  const { enqueue, tokens, host } = useThumbnailQueue<AssetVersion>({
    theme,
    prepare: async (asset, signal) => {
      if (asset.visualUrl || asset.fallbackVisuals?.length) return { asset };
      if (!asset.hasFallbackVisuals) return null;
      const [kind, name] = asset.assetId.split('/');
      const detail = await getAssetDetail(kind, name, String(asset.rev), signal);
      return detail.visualUrl || detail.fallbackVisuals?.length ? { asset: detail } : null;
    },
  });
  return <>
    {children(asset => {
      const key = `${asset.id}:${asset.contentHash}:${theme}`;
      const path = thumbnailQuery('asset', asset.id, asset.contentHash, theme);
      const renderable = Boolean(asset.visualUrl || asset.fallbackVisuals?.length || asset.hasFallbackVisuals);
      return <StaticThumbnail key={key}
        url={asset.thumbnailUrl ?? '/api/v1' + path}
        href={`/assets/${asset.assetId.split('/').map(encodeURIComponent).join('/')}/${encodeURIComponent(asset.id.split('@')[1])}`}
        linkLabel={`打开 ${asset.id}`}
        label={`${asset.id} 的静态预览`}
        declared={Boolean(asset.thumbnailUrl)}
        refreshToken={tokens[key] ?? 0}
        onMissing={renderable ? () => enqueue({ key, path, item: asset }) : undefined} />;
    })}
    {host}
  </>;
}
