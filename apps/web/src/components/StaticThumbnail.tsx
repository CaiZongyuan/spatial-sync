import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import {
  cacheThumbnailBlob,
  getCachedThumbnailUrl,
  invalidateCachedThumbnail,
  loadThumbnail,
  ThumbnailError,
} from '../lib/thumbnail-requests';

export function StaticThumbnail({ url, label, href, linkLabel, declared = false, refreshToken = 0, onMissing }: {
  url: string; label: string; href?: string; linkLabel?: string; declared?: boolean; refreshToken?: number; onMissing?: () => void;
}) {
  const element = useRef<HTMLSpanElement>(null);
  const initialCached = getCachedThumbnailUrl(url);
  const [visible, setVisible] = useState(Boolean(initialCached));
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ url: string; image?: string; error?: string } | undefined>(
    () => (initialCached ? { url, image: initialCached } : undefined),
  );
  const onMissingRef = useRef(onMissing);
  onMissingRef.current = onMissing;
  useEffect(() => {
    const cached = getCachedThumbnailUrl(url);
    if (cached) {
      setVisible(true);
      setResult({ url, image: cached });
      return;
    }
    if (!element.current || visible) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: '200px' });
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [url, visible, refreshToken]);
  useEffect(() => {
    if (!visible) return;
    const cached = getCachedThumbnailUrl(url);
    if (cached) {
      setResult({ url, image: cached });
      return;
    }
    const controller = new AbortController();
    setResult(undefined);
    void loadThumbnail(url, controller.signal).then(blob => {
      if (controller.signal.aborted) return;
      const objectUrl = cacheThumbnailBlob(url, blob);
      setResult({ url, image: objectUrl });
    }).catch(error => {
      if (controller.signal.aborted) return;
      const code = error instanceof ThumbnailError ? error.code : 'failed';
      setResult({ url, error: code });
      if (code === 'missing' && !declared) onMissingRef.current?.();
    });
    return () => { controller.abort(); };
  }, [visible, url, attempt, declared, refreshToken]);
  const current = result?.url === url ? result : undefined;
  const image = current?.image ? <img src={current.image} data-thumbnail-source={url} alt={label}
    onError={() => { invalidateCachedThumbnail(url); setResult({ url, error: 'failed' }); }} /> : null;
  const failed = Boolean(current?.error);
  return <span ref={element} style={{ display: 'block', width: '100%', height: '100%' }}>{image ?
    href ? <a href={href} aria-label={linkLabel} style={{ display: 'block', width: '100%', height: '100%' }}>{image}</a> : image :
    <span className="scene-thumbnail-unavailable"><Icon name="scenes" className="thumbnail-empty-icon" />
      {current?.error === 'timeout' ? '静态图加载超时' : failed && (declared || current?.error !== 'missing') ? '预览加载失败' : '暂无静态预览'}
      {failed && <button type="button" onClick={() => { invalidateCachedThumbnail(url); setAttempt(value => value + 1); }}>重试预览</button>}</span>}
  </span>;
}
