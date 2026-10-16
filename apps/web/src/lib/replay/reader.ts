import type { ServerMessage } from '../protocol';

export interface RecordingSegment {
  file: string;
  sceneRevision?: string;
  startSampleTimeMs: number;
  endSampleTimeMs: number;
  startSimTime: number;
  endSimTime: number;
  bytes: number;
}
export interface Recording {
  formatVersion: 1;
  protocolVersion: 2;
  sessionId: string;
  sceneId: string;
  sceneRevision: string;
  createdAt: string;
  status: 'recording' | 'complete' | 'incomplete';
  durationMs?: number;
  segments: RecordingSegment[];
  revisions?: Record<string, { manifest: string; catalog: string; revisionNumericId: number }>;
}
export interface LogRecord {
  kind: 1 | 2 | 3;
  tMs: number;
  atMs: number;
  payload: ArrayBuffer;
  message?: ServerMessage;
  gap?: { count: number; startSampleTimeMs: number; endSampleTimeMs: number };
}

export const recordingUrl = (relative: string, documentUrl: string) => new URL(relative, documentUrl).href;

export function readSegment(raw: ArrayBuffer) {
  const view = new DataView(raw);
  if (raw.byteLength < 6 || view.getUint32(0, true) !== 0x474c574c || view.getUint16(4, true) !== 1)
    throw new Error('Unsupported recording header/version');
  const records: LogRecord[] = [];
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let offset = 6;
  let clock = -Infinity;
  while (offset < raw.byteLength) {
    if (raw.byteLength - offset < 13) return { records, truncated: true };
    const length = view.getUint32(offset, true);
    const kind = view.getUint8(offset + 4);
    const tMs = view.getFloat64(offset + 5, true);
    if (length < 9 || length > 64 * 1024 * 1024 || ![1, 2, 3].includes(kind) || !Number.isFinite(tMs))
      throw new Error(`Invalid recording record at ${offset}`);
    if (offset + 4 + length > raw.byteLength) return { records, truncated: true };
    const payload = raw.slice(offset + 13, offset + 4 + length);
    clock = Math.max(clock, tMs);
    records.push({
      kind: kind as LogRecord['kind'], tMs, atMs: clock, payload,
      ...(kind === 2 ? { message: JSON.parse(decoder.decode(payload)) as ServerMessage } : {}),
      ...(kind === 3 ? { gap: JSON.parse(decoder.decode(payload)) as LogRecord['gap'] } : {}),
    });
    offset += 4 + length;
  }
  return { records, truncated: false };
}
