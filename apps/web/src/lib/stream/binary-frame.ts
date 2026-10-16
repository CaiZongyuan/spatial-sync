/** Full transform frames and recovery snapshots share the fixed NSYN layout. */
export interface BinaryFrame {
  type: 'transform.frame' | 'stream.snapshot';
  epoch: number;
  seq: number;
  simTime: number;
  sampleTimeMs: number;
  count: number;
  ids: Uint32Array;
  values: Float32Array;
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
  if ((kind !== 1 && kind !== 2) || view.getUint32(8, true) !== revisionNumericId)
    return;
  const offset = 44;
  if (bytes.byteLength < offset) return;
  const seq64 = view.getBigUint64(16, true);
  if (seq64 > BigInt(Number.MAX_SAFE_INTEGER)) return;
  const simTime = view.getFloat64(24, true),
    sampleTimeMs = view.getFloat64(32, true);
  const count = view.getUint32(40, true);
  if (
    count > maxBodies ||
    bytes.byteLength !== offset + count * 32 ||
    !Number.isFinite(simTime) ||
    simTime < 0 ||
    !Number.isFinite(sampleTimeMs) ||
    sampleTimeMs < 0
  )
    return;
  let ids = new Uint32Array(bytes, offset, count * 8);
  let values = new Float32Array(bytes, offset, count * 8);
  if (!nativeLittleEndian) {
    ids = new Uint32Array(count * 8);
    values = new Float32Array(count * 8);
    for (let word = 0; word < count * 8; word++) {
      ids[word] = view.getUint32(offset + 4 * word, true);
      values[word] = view.getFloat32(offset + 4 * word, true);
    }
  }
  return {
    type: kind === 2 ? 'stream.snapshot' : 'transform.frame',
    epoch: view.getUint32(12, true),
    seq: Number(seq64),
    simTime,
    sampleTimeMs,
    count,
    ids,
    values,
  };
}
