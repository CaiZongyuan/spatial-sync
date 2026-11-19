import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { ApprovalInboxContext } from '../lib/approvals';
import type { SceneVersion } from '../lib/library';
import type { AssetVersion } from '../lib/asset-library';
import { saveThumbnail, thumbnailQuery, useThumbnailTheme } from '../lib/thumbnails';
import { cacheThumbnailBlob } from '../lib/thumbnail-requests';
import { CaptureRendererHost } from './ThumbnailQueue';

export function GenerateThumbnail({ asset, scene }: { asset?: AssetVersion; scene?: SceneVersion }) {
  const value = scene ?? asset!;
  const theme = useThumbnailTheme();
  const permissions = useContext(ApprovalInboxContext);
  const path = thumbnailQuery(scene ? 'scene' : 'asset', value.id, value.contentHash, theme, scene?.preview.source);
  const [making, setMaking] = useState(false), [message, setMessage] = useState('');
  const controller = useRef<AbortController | undefined>(undefined);
  useEffect(() => {
    setMaking(false); setMessage('');
    return () => controller.current?.abort();
  }, [path]);
  useEffect(() => {
    if (!making) return;
    // Large readonly scenes may need dozens of seconds to load all verified GLBs.
    const timer = setTimeout(() => {
      controller.current?.abort(); setMaking(false); setMessage('生成缩略图超时，可以重试。');
    }, 120000);
    return () => clearTimeout(timer);
  }, [making]);
  const done = useCallback((png?: string) => {
    const pending = controller.current;
    if (!pending || pending.signal.aborted) return;
    if (!png) { setMaking(false); setMessage('预览不可用，未保存缩略图。'); return; }
    void saveThumbnail(path, png, scene?.preview.capturedAt, pending.signal).then(({ blob }) => {
      if (pending.signal.aborted) return;
      cacheThumbnailBlob('/api/v1' + path, blob);
      setMessage('已保存列表缩略图。');
    }).catch(error => { if (!pending.signal.aborted) setMessage((error as Error).message); })
      .finally(() => { if (!pending.signal.aborted) setMaking(false); });
  }, [path, scene?.preview.capturedAt]);
  const unavailable = scene ? (scene.preview.status !== 'available' && scene.preview.status !== 'stale') : !(asset?.visualUrl || asset?.fallbackVisuals?.length);
  return <div><button disabled={making || unavailable || permissions.inbox?.role !== 'operator' || Boolean(permissions.error)} onClick={() => {
    controller.current?.abort(); controller.current = new AbortController(); setMessage(''); setMaking(true);
  }}>{making ? '正在生成缩略图…' : '生成列表缩略图'}</button>
    {message && <p role="status">{message}</p>}
    {making && <CaptureRendererHost asset={asset} scene={scene} done={done} />}
  </div>;
}
