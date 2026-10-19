import { useCallback, useMemo, type ReactNode } from 'react';
import type { AssetVersion } from '../lib/asset-library';
import { useViewerTheme } from '../lib/scene/viewer-theme';
import { AssetModelViewport } from './AssetModelViewport';
import { ThumbnailQueue } from './ThumbnailQueue';

export function AssetThumbnails({ assets, children }: { assets: AssetVersion[]; children: (thumbnail: (asset: AssetVersion) => ReactNode) => ReactNode }) {
  const themeKey = JSON.stringify(useViewerTheme());
  const keyFor = useCallback((asset: AssetVersion) => ['asset', asset.id, asset.contentHash, themeKey].join(':'), [themeKey]);
  const entries = useMemo(() => assets.map(asset => ({ key: keyFor(asset), label: `${asset.id} 的素材模型`, image: asset.thumbnailUrl,
    reason: asset.visualUrl ? undefined : '素材包没有可用的视觉文件',
    render: asset.visualUrl ? (capture: (png?: string) => void) => <AssetModelViewport url={asset.visualUrl!} capture={capture} captureKey={keyFor(asset)} /> : undefined,
  })), [assets, keyFor]);
  return <ThumbnailQueue entries={entries}>{thumbnail => children(asset => thumbnail(keyFor(asset)))}</ThumbnailQueue>;
}
