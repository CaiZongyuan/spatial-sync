import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';

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
  useEffect(() => {
    setResults(previous => new Map(entries.filter(entry => previous.has(entry.key) || images.has(entry.key))
      .map(entry => [entry.key, previous.get(entry.key) ?? images.get(entry.key)])));
  }, [entries]);
  const next = entries.find(entry => entry.render && !entry.image && !images.has(entry.key) && !results.has(entry.key));
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
    const image = entry.image ?? results.get(entryKey) ?? images.get(entryKey);
    return image ? <img src={image} alt={entry.label} /> : <span className="scene-thumbnail-unavailable" title={entry.reason ?? undefined}>
      <span aria-hidden="true">◇</span>{entry.render ? results.has(entryKey) ? '预览加载失败' : '生成预览…' : '暂无静态预览'}</span>;
  })}{next && <div className="scene-thumbnail-renderer" aria-hidden="true">{next.render!(done)}</div>}</>;
}
