import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';

export function StaticThumbnail({ url, label, declared = false }: { url: string; label: string; declared?: boolean }) {
  const element = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false), [failed, setFailed] = useState<string>();
  useEffect(() => {
    if (!element.current || visible) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: '200px' });
    observer.observe(element.current);
    return () => observer.disconnect();
  }, [visible]);
  return <span ref={element} style={{ display: 'block', width: '100%', height: '100%' }}>{visible && failed !== url ?
    <img src={url} alt={label} loading="lazy" onError={() => setFailed(url)} /> :
    <span className="scene-thumbnail-unavailable"><Icon name="scenes" className="thumbnail-empty-icon" />
      {failed === url && declared ? '预览加载失败' : '暂无静态预览'}</span>}
  </span>;
}
