/** NSYN kinds 1/2 retain the IPC header; kinds 3/4/5 carry a scoped WS extension. */
export interface BinaryFrame {
  type: 'transform.frame' | 'stream.snapshot' | 'transform.delta';
  epoch: number;
  seq: number;
  simTime: number;
  sampleTimeMs: number;
  count: number;
  ids: Uint32Array;
  values: Float32Array;
  subscriptionId?: number;
  keyframe?: boolean;
  baseSeq?: number;
  removedStreamIds?: Uint32Array;
}
const nativeLittleEndian = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;
export function decodeBinaryFrame(
  bytes: ArrayBuffer,
  revisionNumericId: number,
  maxBodies: number,
): BinaryFrame | undefined {
  if (bytes.byteLength < 44) return;
  const view = new DataView(bytes);
  if (view.getUint32(0, true) !== 0x4e59534e || view.getUint16(4, true) !== 1)
    return;
  const kind = view.getUint16(6, true);
  if (kind < 1 || kind > 5 || view.getUint32(8, true) !== revisionNumericId)
    return;
  const offset = kind === 3 ? 60 : kind >= 4 ? 48 : 44;
  if (bytes.byteLength < offset) return;
  const seq64 = view.getBigUint64(16, true);
  if (seq64 > BigInt(Number.MAX_SAFE_INTEGER)) return;
  const simTime = view.getFloat64(24, true),
    sampleTimeMs = view.getFloat64(32, true);
  const count = view.getUint32(40, true);
  const removedCount = kind === 3 ? view.getUint32(56, true) : 0;
  const base64 = kind === 3 ? view.getBigUint64(48, true) : undefined;
  if (
    count > maxBodies ||
    removedCount > maxBodies ||
    bytes.byteLength !== offset + count * 32 + removedCount * 4 ||
    !Number.isFinite(simTime) ||
    simTime < 0 ||
    !Number.isFinite(sampleTimeMs) ||
    sampleTimeMs < 0 ||
    (base64 !== undefined &&
      (base64 >= seq64 || base64 > BigInt(Number.MAX_SAFE_INTEGER)))
  )
    return;
  let ids = new Uint32Array(bytes, offset, count * 8);
  let values = new Float32Array(bytes, offset, count * 8);
  let removedStreamIds = new Uint32Array(
    bytes,
    offset + count * 32,
    removedCount,
  );
  if (!nativeLittleEndian) {
    ids = new Uint32Array(count * 8);
    values = new Float32Array(count * 8);
    for (let word = 0; word < count * 8; word++) {
      ids[word] = view.getUint32(offset + 4 * word, true);
      values[word] = view.getFloat32(offset + 4 * word, true);
    }
    removedStreamIds = Uint32Array.from({ length: removedCount }, (_, i) =>
      view.getUint32(offset + count * 32 + i * 4, true),
    );
  }
  return {
    type:
      kind === 3
        ? 'transform.delta'
        : kind === 2 || kind === 5
          ? 'stream.snapshot'
          : 'transform.frame',
    epoch: view.getUint32(12, true),
    seq: Number(seq64),
    simTime,
    sampleTimeMs,
    count,
    ids,
    values,
    ...(kind >= 3 ? { subscriptionId: view.getUint32(44, true) } : {}),
    ...(kind >= 4 ? { keyframe: true } : {}),
    ...(base64 !== undefined
      ? { baseSeq: Number(base64), removedStreamIds }
      : {}),
  };
}
