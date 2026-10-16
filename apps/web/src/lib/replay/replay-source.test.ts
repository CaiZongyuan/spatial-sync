import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, expect, it, vi } from 'vitest';
import { ReplaySource } from './replay-source';

const fixture = fileURLToPath(new URL('../../../../../tests/fixtures/recordings/v1/', import.meta.url));
const golden = JSON.parse(readFileSync(fixture + '/expected.json', 'utf8')) as {
  tubeKey: string; robotKey: string;
  samples: { timeMs: number; seq: number; tubePose: { p: number[]; q: number[] };
    tubeLocation: string; robotJoints: number[] }[];
};
let source: ReplaySource;

afterEach(() => {
  source?.stop();
  vi.unstubAllGlobals();
});

it('reconstructs ten independently extracted log states and poses from the published v1 fixture', async () => {
  const urls: string[] = [];
  vi.stubGlobal('location', { href: 'https://example.org/replay/v1' });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    const name = new URL(url).pathname.split('/v1/')[1];
    return new Response(new Uint8Array(readFileSync(fixture + '/' + name)));
  }));
  source = new ReplaySource('v1', 'https://example.org/files/v1/recording.json');
  await source.start();
  expect(source.readOnly).toBe(true);
  expect(source.info.getMetadata().ready).toBe(true);
  for (const sample of golden.samples) {
    await source.seek(sample.timeMs);
    expect(source.buffer.latestSample?.seq).toBe(sample.seq);
    expect(source.buffer.readLatest(golden.tubeKey)).toEqual(sample.tubePose);
    expect(source.info.getEntity(golden.tubeKey)?.values.location).toBe(sample.tubeLocation);
    expect(source.info.getEntity(golden.robotKey)?.values.jointPositions).toEqual(sample.robotJoints);
  }
  expect(source.getPlayback().playing).toBe(false);
  expect(urls.every((url) => url.startsWith('https://example.org/files/v1/'))).toBe(true);
  expect(urls).toHaveLength(4); // One cached segment and its metadata/manifest/catalog.
  expect(() => source.action(golden.robotKey, 'home', {})).toThrow(/read-only/);
});
