import { afterEach, expect, it, vi } from 'vitest';
import { loadThumbnail } from './thumbnail-requests';

afterEach(() => vi.unstubAllGlobals());

it('aborts an error response with an unfinished body before releasing its slot', async () => {
  let requestSignal: AbortSignal | undefined;
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    requestSignal = options.signal as AbortSignal;
    return new Response(new ReadableStream(), { status: 503 });
  }));
  await expect(loadThumbnail('/stalled-error.png', new AbortController().signal)).rejects.toMatchObject({ code: 'failed' });
  expect(requestSignal?.aborted).toBe(true);
});
