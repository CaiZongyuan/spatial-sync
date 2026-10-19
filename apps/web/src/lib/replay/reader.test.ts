import { describe, expect, it } from 'vitest';
import { readRecording, readSegment, recordedScene, recordingUrl } from './reader';

const metadata = () => ({ formatVersion: 2, protocolVersion: 2, kind: 'session', sceneId: 'lab-demo',
  sceneVersion: 'lab-demo@2', sceneContentHash: 'a'.repeat(64), segments: [] });

describe('recording metadata compatibility', () => {
  it('retains the actual optional session seed without inferring one for older files', () => {
    expect(readRecording(metadata())).not.toHaveProperty('seed');
    expect(readRecording({ ...metadata(), protocolMinorVersion: 1, seed: 43 })).toMatchObject({ seed: 43 });
    for (const seed of [true, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])
      expect(() => readRecording({ ...metadata(), seed })).toThrow(/seed/);
  });
  it('derives the v1 version without changing its document and preserves v2 exact identity', () => {
    const legacy = { formatVersion: 1, protocolVersion: 2, sceneId: 'lab-demo', segments: [] };
    expect(readRecording(legacy)).toMatchObject({ kind: 'session', sceneVersion: 'lab-demo@1', sceneContentHash: null });
    expect(legacy).not.toHaveProperty('sceneVersion');
    expect(recordedScene(readRecording(metadata()))).toBe('lab-demo@2');
  });

  it.each([
    { sceneVersion: null }, { sceneVersion: 'lab-demo' }, { sceneVersion: 'lab-demo@0' },
    { sceneVersion: 'other@2' }, { sceneContentHash: null }, { sceneContentHash: 'bad' },
    { kind: 'unknown' }, { formatVersion: true }, { segments: [null] }, { segments: {} },
  ])('rejects malformed v2 metadata %j', patch => {
    expect(() => readRecording({ ...metadata(), ...patch })).toThrow();
  });

  it.each(['sceneVersion', 'sceneContentHash', 'kind'])('does not infer missing v2 %s', field => {
    const value: Record<string, unknown> = metadata();
    delete value[field];
    expect(() => readRecording(value)).toThrow();
  });

  it('keeps live identity null and accepts optional segment index hints', () => {
    const value = { ...metadata(), kind: 'live', sceneVersion: null, sceneContentHash: null,
      segments: [{ file: 'seg-000001.bin', propertyIndex: 'seg-000001.index.json' }] };
    expect(readRecording(value)).toMatchObject({ kind: 'live', sceneVersion: null, sceneContentHash: null });
    expect(recordedScene(readRecording(value))).toBe('Live');
    expect(() => readRecording({ ...value, sceneVersion: 'live@1' })).toThrow();
    expect(() => readRecording({ ...value, sceneContentHash: 'a'.repeat(64) })).toThrow();
    value.segments[0].propertyIndex = '../private.json';
    expect(() => readRecording(value)).toThrow(/index/);
  });
});

function segment(records: { kind: number; time: number; value: string }[]) {
  const payloads = records.map((record) => new TextEncoder().encode(record.value));
  const raw = new Uint8Array(6 + payloads.reduce((sum, payload) => sum + 13 + payload.length, 0));
  raw.set([76, 87, 76, 71, 1, 0]);
  const view = new DataView(raw.buffer);
  let offset = 6;
  records.forEach((record, index) => {
    view.setUint32(offset, 9 + payloads[index].length, true);
    view.setUint8(offset + 4, record.kind);
    view.setFloat64(offset + 5, record.time, true);
    raw.set(payloads[index], offset + 13);
    offset += 13 + payloads[index].length;
  });
  return raw.buffer;
}

describe('v1 log reader', () => {
  it('pins literal header/length and keeps FIFO using a prefix maximum clock', () => {
    const raw = segment([
      { kind: 2, time: 1000, value: '{"type":"server.error","code":"first"}' },
      { kind: 2, time: 900, value: '{"type":"server.error","code":"second"}' },
      { kind: 3, time: 1100, value: '{"count":2}' },
    ]);
    const result = readSegment(raw);
    expect(result.records.map((record) => record.tMs)).toEqual([1000, 900, 1100]);
    expect(result.records.map((record) => record.atMs)).toEqual([1000, 1000, 1100]);
    expect(result.records[1].message).toMatchObject({ code: 'second' });
    expect(result.truncated).toBe(false);
    expect(readSegment(raw.slice(0, -2)).records).toHaveLength(2);
    expect(readSegment(raw.slice(0, -2)).truncated).toBe(true);
  });

  it('rejects unsupported version and corrupt kind rather than skipping reliable records', () => {
    const raw = segment([{ kind: 2, time: 1, value: '{}' }]);
    new DataView(raw).setUint16(4, 2, true);
    expect(() => readSegment(raw)).toThrow(/version/);
    new DataView(raw).setUint16(4, 1, true);
    new DataView(raw).setUint8(10, 8);
    expect(() => readSegment(raw)).toThrow(/record/);
  });

  it('resolves all document and mesh paths relative to recording.json', () => {
    expect(recordingUrl('manifest.json', 'https://example.org/archive/id/recording.json'))
      .toBe('https://example.org/archive/id/manifest.json');
    expect(recordingUrl('assets/model.glb', 'https://example.org/archive/id/recording.json'))
      .toBe('https://example.org/archive/id/assets/model.glb');
  });
});
