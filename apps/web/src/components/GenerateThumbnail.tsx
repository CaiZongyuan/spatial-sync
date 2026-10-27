import { Component, lazy, Suspense, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { ApprovalInboxContext } from '../lib/approvals';
import type { SceneVersion } from '../lib/library';
import type { AssetVersion } from '../lib/asset-library';
import { saveThumbnail, thumbnailQuery, useThumbnailTheme } from '../lib/thumbnails';

const Renderer = lazy(() => import('./ThumbnailCaptureRenderer'));
class CaptureFailure extends Component<{ children: ReactNode; failed: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.failed(); }
  render() { return this.state.failed ? null : this.props.children; }
}

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
    const timer = setTimeout(() => {
      controller.current?.abort(); setMaking(false); setMessage('生成缩略图超时，可以重试。');
    }, 15000);
    return () => clearTimeout(timer);
  }, [making]);
  const done = useCallback((png?: string) => {
    const pending = controller.current;
    if (!pending || pending.signal.aborted) return;
    if (!png) { setMaking(false); setMessage('预览不可用，未保存缩略图。'); return; }
    void saveThumbnail(path, png, scene?.preview.capturedAt, pending.signal).then(() => {
      if (!pending.signal.aborted) setMessage('已保存列表缩略图。');
    }).catch(error => { if (!pending.signal.aborted) setMessage((error as Error).message); })
      .finally(() => { if (!pending.signal.aborted) setMaking(false); });
  }, [path, scene?.preview.capturedAt]);
  const unavailable = scene ? scene.preview.status !== 'available' : !asset?.visualUrl;
  return <div><button disabled={making || unavailable || permissions.inbox?.role !== 'operator' || Boolean(permissions.error)} onClick={() => {
    controller.current?.abort(); controller.current = new AbortController(); setMessage(''); setMaking(true);
  }}>{making ? '正在生成缩略图…' : '生成列表缩略图'}</button>
    {message && <p role="status">{message}</p>}
    {making && <div className="scene-thumbnail-renderer" aria-hidden="true"><CaptureFailure failed={() => done()}>
      <Suspense fallback={null}><Renderer {...{ asset, scene, done }} /></Suspense>
    </CaptureFailure></div>}
  </div>;
}
