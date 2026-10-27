import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import { loadThumbnail, ThumbnailError } from '../lib/thumbnail-requests';

export function StaticThumbnail({ url, label, href, linkLabel, declared = false }: {
  url: string; label: string; href?: string; linkLabel?: string; declared?: boolean;
}) {
  const element = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false), [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ url: string; image?: string; error?: string }>();
  useEffect(() => {
    if (!element.current || visible) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: '200px' });
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [visible]);
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController(); let owned: string | undefined;
    setResult(undefined);
    void loadThumbnail(url, controller.signal).then(blob => {
      if (controller.signal.aborted) return;
      owned = URL.createObjectURL(blob); setResult({ url, image: owned });
    }).catch(error => { if (!controller.signal.aborted) setResult({ url, error: error instanceof ThumbnailError ? error.code : 'failed' }); });
    return () => { controller.abort(); if (owned) URL.revokeObjectURL(owned); };
  }, [visible, url, attempt]);
  const current = result?.url === url ? result : undefined;
  const image = current?.image ? <img src={current.image} data-thumbnail-source={url} alt={label}
    onError={() => setResult({ url, error: 'failed' })} /> : null;
  const failed = Boolean(current?.error);
  return <span ref={element} style={{ display: 'block', width: '100%', height: '100%' }}>{image ?
    href ? <a href={href} aria-label={linkLabel} style={{ display: 'block', width: '100%', height: '100%' }}>{image}</a> : image :
    <span className="scene-thumbnail-unavailable"><Icon name="scenes" className="thumbnail-empty-icon" />
      {current?.error === 'timeout' ? '静态图加载超时' : failed && (declared || current?.error !== 'missing') ? '预览加载失败' : '暂无静态预览'}
      {failed && <button type="button" onClick={() => setAttempt(value => value + 1)}>重试预览</button>}</span>}
  </span>;
}
