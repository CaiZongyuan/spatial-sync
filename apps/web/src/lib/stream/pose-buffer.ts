import { Quaternion } from 'three';
import { decodeBinaryFrame } from './binary-frame';
import type {
  SceneManifest,
  TransformFrame,
  Snapshot,
  Pose,
} from '../protocol';

type WireFrame = TransformFrame | Snapshot;
type FrameHeader = Pick<
  WireFrame,
  'type' | 'sceneRevision' | 'epoch' | 'seq' | 'simTime' | 'sampleTimeMs'
>;
type BufferedFrame = {
  sample: number;
  simTime: number;
  seq: number;
  data: Float64Array;
  valid: Uint8Array;
};

/** The renderer owns this mutable buffer. Network frames never enter React state. */
export class PoseBuffer {
  private frames: BufferedFrame[] = [];
  private offsets: { received: number; offset: number }[] = [];
  private indices = new Map<number, number>();
  private keys = new Map<string, number>();
  private displayed = new Float64Array();
  private authoritative?: BufferedFrame;
  private qa = new Quaternion();
  private qb = new Quaternion();
  revision = '';
  private revisionNumericId = 0;
  epoch = -1;
  lastSeq = -1;
  displayedSeq = -1;
  displayedSimTime = 0;
  displayedSampleTimeMs = 0;
  receivedAt = 0;
  grantedRateHz = 30;
  skippedNonFinite = 0;
  discardedFrames = 0;
  invalidBinaryFrames = 0;
  discontinuities = 0;
  paused = false;
  awaitingSnapshot = true;

  configure(manifest: SceneManifest) {
    this.revision = manifest.sceneRevision;
    this.revisionNumericId = manifest.revisionNumericId;
    this.epoch = manifest.epoch;
    this.lastSeq = -1;
    this.displayedSeq = -1;
    this.authoritative = undefined;
    this.indices.clear();
    this.keys.clear();
    for (const node of manifest.nodes) {
      if (node.kind !== 'body') continue;
      const index = this.indices.size;
      this.indices.set(node.streamId, index);
      this.keys.set(node.entityKey, index);
    }
    this.displayed = new Float64Array(this.indices.size * 7);
    for (let i = 6; i < this.displayed.length; i += 7) this.displayed[i] = 1;
    this.resetTiming();
    this.awaitingSnapshot = true;
  }

  resetTiming() {
    this.frames.length = 0;
    this.offsets.length = 0;
  }

  expectSnapshot() {
    this.resetTiming();
    this.awaitingSnapshot = true;
  }

  push(frame: WireFrame, received: number): boolean {
    return this.ingest(frame, received, (data, valid) => {
      for (const pose of frame.poses) {
        this.writePose(
          data,
          valid,
          this.indices.get(pose[0]),
          pose[1],
          pose[2],
          pose[3],
          pose[4],
          pose[5],
          pose[6],
          pose[7],
        );
      }
    });
  }

  pushBinary(bytes: ArrayBuffer, received: number): boolean {
    const frame = decodeBinaryFrame(
      bytes,
      this.revisionNumericId,
      this.indices.size,
    );
    if (!frame) {
      this.invalidBinaryFrames++;
      return false;
    }
    return this.ingest(
      { ...frame, sceneRevision: this.revision },
      received,
      (data, valid) => {
        const values = frame.values;
        for (let body = 0; body < frame.count; body++) {
          const word = body * 8;
          this.writePose(
            data,
            valid,
            this.indices.get(frame.ids[word]),
            values[word + 1],
            values[word + 2],
            values[word + 3],
            values[word + 4],
            values[word + 5],
            values[word + 6],
            values[word + 7],
          );
        }
      },
    );
  }

  private writePose(
    data: Float64Array,
    valid: Uint8Array,
    index: number | undefined,
    px: number,
    py: number,
    pz: number,
    qx: number,
    qy: number,
    qz: number,
    qw: number,
  ) {
    if (index === undefined) return;
    const norm = Math.hypot(qx, qy, qz, qw);
    if (
      !Number.isFinite(px) ||
      !Number.isFinite(py) ||
      !Number.isFinite(pz) ||
      !Number.isFinite(qx) ||
      !Number.isFinite(qy) ||
      !Number.isFinite(qz) ||
      !Number.isFinite(qw) ||
      !Number.isFinite(norm) ||
      norm < 1e-12
    ) {
      this.skippedNonFinite++;
      valid[index] = this.authoritative?.valid[index] ?? 0;
      if (valid[index])
        data.set(
          this.authoritative!.data.subarray(index * 7, index * 7 + 7),
          index * 7,
        );
      return;
    }
    valid[index] = 1;
    const start = index * 7;
    data[start] = px;
    data[start + 1] = py;
    data[start + 2] = pz;
    data[start + 3] = qx / norm;
    data[start + 4] = qy / norm;
    data[start + 5] = qz / norm;
    data[start + 6] = qw / norm;
  }

  private ingest(
    frame: FrameHeader,
    received: number,
    write: (data: Float64Array, valid: Uint8Array) => void,
  ): boolean {
    const snapshot = frame.type === 'stream.snapshot';
    const repeatedRecoverySnapshot =
      snapshot &&
      this.awaitingSnapshot &&
      frame.seq === this.lastSeq &&
      frame.epoch === this.epoch;
    if (
      frame.sceneRevision !== this.revision ||
      !Number.isSafeInteger(frame.seq) ||
      !Number.isFinite(frame.sampleTimeMs) ||
      !Number.isFinite(frame.simTime) ||
      (frame.seq <= this.lastSeq && !repeatedRecoverySnapshot) ||
      frame.epoch < this.epoch ||
      (!snapshot && (this.awaitingSnapshot || frame.epoch !== this.epoch))
    ) {
      this.discardedFrames++;
      return false;
    }
    if (snapshot) {
      this.resetTiming();
      this.epoch = frame.epoch;
      this.awaitingSnapshot = false;
    }
    const prior = this.frames.at(-1);
    const gap =
      prior &&
      frame.sampleTimeMs - prior.sample > 3 * (1000 / this.grantedRateHz);
    if (gap) {
      this.frames.length = 0;
      this.discontinuities++;
    }
    const data = new Float64Array(prior?.data ?? this.displayed);
    const valid = new Uint8Array(this.indices.size);
    write(data, valid);
    this.authoritative = {
      sample: frame.sampleTimeMs,
      simTime: frame.simTime,
      seq: frame.seq,
      data,
      valid,
    };
    this.frames.push(this.authoritative);
    if (this.frames.length > 64) this.frames.shift();
    this.offsets.push({ received, offset: received - frame.sampleTimeMs });
    this.offsets = this.offsets.filter(
      (item) => received - item.received <= 2000,
    );
    this.lastSeq = frame.seq;
    this.receivedAt = received;
    if (snapshot || gap || this.frames.length === 1 || this.paused)
      this.applyLatest();
    return true;
  }

  get clockOffset() {
    return this.offsets.length
      ? Math.min(...this.offsets.map((item) => item.offset))
      : 0;
  }
  get frameCount() {
    return this.frames.length;
  }
  get playoutDelay() {
    return 2 * (1000 / this.grantedRateHz);
  }

  applyLatest() {
    const latest = this.frames.at(-1);
    if (!latest) return;
    this.displayed.set(latest.data);
    this.displayedSeq = latest.seq;
    this.displayedSimTime = latest.simTime;
    this.displayedSampleTimeMs = latest.sample;
  }

  sample(now: number): Float64Array {
    if (!this.frames.length) return this.displayed;
    const target = now - this.clockOffset - this.playoutDelay;
    if (this.paused || target >= this.frames.at(-1)!.sample) {
      this.applyLatest();
      return this.displayed;
    }
    let a = this.frames[0];
    let b = a;
    for (const candidate of this.frames) {
      if (candidate.sample <= target) a = candidate;
      if (candidate.sample >= target) {
        b = candidate;
        break;
      }
    }
    const t =
      a === b
        ? 0
        : Math.max(0, Math.min(1, (target - a.sample) / (b.sample - a.sample)));
    for (let i = 0; i < this.displayed.length; i += 7) {
      if (!a.valid[i / 7] || !b.valid[i / 7]) {
        this.displayed.set(this.authoritative!.data.subarray(i, i + 7), i);
        continue;
      }
      for (let j = 0; j < 3; j++)
        this.displayed[i + j] =
          a.data[i + j] + (b.data[i + j] - a.data[i + j]) * t;
      this.qa.fromArray(a.data, i + 3);
      this.qb.fromArray(b.data, i + 3);
      // Three's SLERP selects the same hemisphere, including q and -q.
      this.qa
        .slerp(this.qb, t)
        .normalize()
        .toArray(this.displayed, i + 3);
    }
    this.displayedSeq = t === 0 ? a.seq : b.seq;
    this.displayedSimTime = a.simTime + (b.simTime - a.simTime) * t;
    this.displayedSampleTimeMs = a.sample + (b.sample - a.sample) * t;
    return this.displayed;
  }

  hasPose(streamId: number) {
    const index = this.indices.get(streamId);
    return index !== undefined && this.authoritative?.valid[index] === 1;
  }
  indexForKey(key: string) {
    return this.keys.get(key);
  }
  get latestSample() {
    const frame = this.authoritative;
    return frame
      ? {
          seq: frame.seq,
          simTime: frame.simTime,
          sampleTimeMs: frame.sample,
          epoch: this.epoch,
        }
      : undefined;
  }
  readLatest(key: string): Pose | undefined {
    return this.readData(key, this.authoritative?.data);
  }
  read(key: string): Pose | undefined {
    return this.readData(
      key,
      this.displayedSeq >= 0 ? this.displayed : undefined,
    );
  }
  private readData(key: string, data?: Float64Array): Pose | undefined {
    const index = this.keys.get(key);
    if (index === undefined || !data || !this.authoritative?.valid[index])
      return;
    const start = index * 7;
    return {
      p: Array.from(data.slice(start, start + 3)) as Pose['p'],
      q: Array.from(data.slice(start + 3, start + 7)) as Pose['q'],
    };
  }
}
