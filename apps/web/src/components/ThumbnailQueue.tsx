import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Icon } from './Icon';

export interface ThumbnailEntry {
  key: string; label: string; image?: string | null; reason?: string | null;
  render?: (capture: (png?: string) => void) => ReactNode;
}
// Scene and asset libraries share a bounded cache and render one queued figure at a time.
const images = new Map<string, string>();
export function ThumbnailQueue({ entries, children }: {
  entries: ThumbnailEntry[]; children: (image: (key: string) => ReactNode) => ReactNode;
}) {
  const [results, setResults] = useState(new Map<string, string | undefined>());
  const [failedImages, setFailedImages] = useState(new Set<string>());
  useEffect(() => {
    setResults(previous => new Map(entries.filter(entry => previous.has(entry.key) || images.has(entry.key))
      .map(entry => [entry.key, previous.get(entry.key) ?? images.get(entry.key)])));
    setFailedImages(previous => new Set(entries.filter(entry => previous.has(entry.key)).map(entry => entry.key)));
  }, [entries]);
  const next = entries.find(entry => entry.render && (!entry.image || failedImages.has(entry.key)) && !images.has(entry.key) && !results.has(entry.key));
  const key = next?.key;
  const activeKey = useRef(key);
  useLayoutEffect(() => {
    activeKey.current = key;
    return () => { activeKey.current = undefined; };
  }, [key]);
  const done = useCallback((png?: string) => {
    if (!key || activeKey.current !== key) return;
    if (png) {
      if (images.size >= 32) images.delete(images.keys().next().value!);
      images.set(key, png);
    }
    setResults(previous => new Map(previous).set(key, png));
  }, [key]);
  return <>{children(entryKey => {
    const entry = entries.find(item => item.key === entryKey)!;
    const image = (failedImages.has(entryKey) ? undefined : entry.image) ?? results.get(entryKey) ?? images.get(entryKey);
    return image ? <img src={image} alt={entry.label} onError={() => setFailedImages(previous => new Set(previous).add(entryKey))} /> :
      entry.render && !results.has(entryKey) ? <span className="scene-thumbnail-skeleton" role="status" aria-label="正在生成预览"><i /><i /></span> :
        <span className="scene-thumbnail-unavailable" title={entry.reason ?? undefined}><Icon name="scenes" className="thumbnail-empty-icon" />
          {entry.render || failedImages.has(entryKey) ? '预览加载失败' : '暂无静态预览'}</span>;
  })}{next && <div className="scene-thumbnail-renderer" aria-hidden="true">{next.render!(done)}</div>}</>;
}
