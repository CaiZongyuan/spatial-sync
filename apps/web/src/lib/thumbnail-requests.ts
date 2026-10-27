/** Image work has its own three slots; a slow image cannot occupy all browser connections. */
const waiting: (() => void)[] = [];
let active = 0;
function pump() { while (active < 3 && waiting.length) waiting.shift()!(); }

function reserve(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      const index = waiting.indexOf(start);
      if (index >= 0) waiting.splice(index, 1);
      reject(new DOMException('Thumbnail retired', 'AbortError')); pump();
    };
    const start = () => {
      signal.removeEventListener('abort', abort);
      if (signal.aborted) { reject(new DOMException('Thumbnail retired', 'AbortError')); return; }
      active++; resolve();
    };
    if (signal.aborted) return abort();
    signal.addEventListener('abort', abort, { once: true }); waiting.push(start); pump();
  });
}
export class ThumbnailError extends Error {
  constructor(readonly code: 'missing' | 'failed' | 'timeout' | 'too_large') { super(code); }
}
export async function loadThumbnail(url: string, signal: AbortSignal): Promise<Blob> {
  await reserve(signal);
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener('abort', cancel, { once: true });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 5000);
  try {
    if (signal.aborted) throw new DOMException('Thumbnail retired', 'AbortError');
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new ThumbnailError(response.status === 404 ? 'missing' : 'failed');
    if (!response.body) throw new ThumbnailError('failed');
    const reader = response.body.getReader(), chunks: Uint8Array<ArrayBuffer>[] = [];
    let bytes = 0;
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      bytes += result.value.byteLength;
      if (bytes > 2 * 1024 * 1024) { await reader.cancel(); throw new ThumbnailError('too_large'); }
      chunks.push(new Uint8Array(result.value));
    }
    return new Blob(chunks, { type: response.headers.get('content-type') ?? 'image/png' });
  } catch (error) {
    if (timedOut && !signal.aborted) throw new ThumbnailError('timeout');
    throw error;
  } finally {
    controller.abort();
    clearTimeout(timer); signal.removeEventListener('abort', cancel); active--; pump();
  }
}
