import { useViewerTheme } from './scene/viewer-theme';
import { request } from './api';

export function useThumbnailTheme(): 'light' | 'dark' {
  useViewerTheme();
  const selected = document.documentElement.dataset.theme;
  return selected === 'dark' || selected !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
export function thumbnailQuery(resource: 'asset' | 'scene', id: string, contentHash: string, theme: string, source?: string) {
  const query = new URLSearchParams({ resource, id, contentHash, theme });
  if (source) query.set('source', source);
  return `/library/thumbnails?${query}`;
}
export async function saveThumbnail(path: string, png: string, capturedAt?: string, signal?: AbortSignal) {
  const payload = await (await fetch(png)).blob();
  return request<{ thumbnailUrl: string }>(`${path}${capturedAt ? `&capturedAt=${encodeURIComponent(capturedAt)}` : ''}`, {
    method: 'POST', headers: { 'Content-Type': 'image/png' }, body: payload, signal,
  });
}
