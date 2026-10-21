import type { ServerMessage } from '../protocol';

export interface RecordingSegment {
  file: string;
  /** Optional telemetry sidecar; ordinary replay scans the segment sequentially. */
  propertyIndex?: string;
  sceneRevision?: string;
  startSampleTimeMs: number;
  endSampleTimeMs: number;
  startSimTime: number;
  endSimTime: number;
  bytes: number;
}
export interface Recording {
  formatVersion: 1 | 2;
  protocolVersion: 2;
  protocolMinorVersion?: number;
  seed?: number;
  sessionId: string;
  sceneId: string | null;
  sceneVersion: string | null;
  sceneContentHash: string | null;
  kind: 'session' | 'live';
  sceneRevision: string;
  createdAt: string;
  status: 'recording' | 'complete' | 'incomplete';
  durationMs?: number;
  segments: RecordingSegment[];
  revisions?: Record<string, { manifest?: string; catalog: string; revisionNumericId: number }>;
}

/** Normalize metadata only. The LWLG segment codec remains binary version 1. */
export function readRecording(value: unknown): Recording {
  if (!value || typeof value !== 'object') throw new Error('Unsupported recording metadata');
  const raw = value as Record<string, unknown>;
  if (![1, 2].includes(raw.formatVersion as number) || raw.protocolVersion !== 2)
    throw new Error('Unsupported recording format/protocol version');
  if (!Array.isArray(raw.segments)) throw new Error('Invalid recording segments');
  if (raw.protocolMinorVersion !== undefined && (!Number.isInteger(raw.protocolMinorVersion)
      || (raw.protocolMinorVersion as number) < 0)) throw new Error('Invalid protocol minor version');
  if (raw.seed !== undefined && (!Number.isSafeInteger(raw.seed) || (raw.seed as number) < 0))
    throw new Error('Invalid session seed');
  if (raw.formatVersion === 1) {
    return { ...raw, kind: 'session', sceneVersion: typeof raw.sceneId === 'string' && raw.sceneId
      ? `${raw.sceneId}@1` : null, sceneContentHash: null } as Recording;
  }
  if (!['kind', 'sceneVersion', 'sceneContentHash'].every(key => Object.hasOwn(raw, key)))
    throw new Error('Recording v2 requires kind, sceneVersion and sceneContentHash');
  if (raw.kind === 'session') {
    if (typeof raw.sceneVersion !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$/.test(raw.sceneVersion))
      throw new Error('Session recording v2 requires an exact scene version');
    if (raw.sceneId !== raw.sceneVersion.split('@')[0]) throw new Error('Recording sceneId and sceneVersion disagree');
    if (typeof raw.sceneContentHash !== 'string' || !/^[a-f\d]{64}$/i.test(raw.sceneContentHash))
      throw new Error('Session recording v2 requires a scene content hash');
  } else if (raw.kind === 'live') {
    if (raw.sceneVersion !== null || raw.sceneContentHash !== null)
      throw new Error('Live recording scene version and content hash must be null');
  } else throw new Error('Unknown recording kind');
  for (const segment of raw.segments) {
    if (!segment || typeof segment !== 'object' || Array.isArray(segment)) throw new Error('Invalid recording segment');
    if (segment.propertyIndex != null && (typeof segment.propertyIndex !== 'string'
      || !/^seg-[0-9]{6}\.index\.json$/.test(segment.propertyIndex)))
      throw new Error('Invalid recording property index reference');
  }
  return { ...raw } as unknown as Recording;
}

export const recordedScene = (recording: Pick<Recording, 'kind' | 'sceneVersion'>) =>
  recording.kind === 'live' ? 'Live' : recording.sceneVersion ?? 'Unknown scene';
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
