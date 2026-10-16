import { afterEach, expect, it, vi } from 'vitest';
import { request } from './api';

afterEach(() => vi.unstubAllGlobals());

it('accepts an empty 204 response when closing a session', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(null, { status: 204 })),
  );
  await expect(
    request<void>('/sessions/example', { method: 'DELETE' }),
  ).resolves.toBeUndefined();
});

it.each([
  [
    {
      detail: {
        code: 'source_unavailable',
        message: 'Install the Newton extra to load this source',
      },
    },
    'Install the Newton extra to load this source',
  ],
  [{ detail: { code: 'unknown_session' } }, 'unknown_session'],
  [{ detail: 'Unknown scene' }, 'Unknown scene'],
  [{ detail: null, message: 'Gateway unavailable' }, 'Gateway unavailable'],
  [null, 'Request failed (503)'],
])('surfaces actionable HTTP error details for %j', async (body, expected) => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(body), { status: 503 })),
  );
  await expect(request('/sessions/missing')).rejects.toThrow(expected);
});
