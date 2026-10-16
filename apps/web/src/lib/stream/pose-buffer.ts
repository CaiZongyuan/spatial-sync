import { Quaternion } from 'three';
import type {
  SceneManifest,
  TransformFrame,
  Snapshot,
  Pose,
} from '../protocol';

type WireFrame = TransformFrame | Snapshot;
type BufferedFrame = {
  sample: number;
  simTime: number;
  seq: number;
  data: Float64Array;
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
  epoch = -1;
  lastSeq = -1;
  displayedSeq = -1;
  displayedSimTime = 0;
  displayedSampleTimeMs = 0;
  receivedAt = 0;
  grantedRateHz = 30;
  skippedNonFinite = 0;
  discardedFrames = 0;
  discontinuities = 0;
  paused = false;
  awaitingSnapshot = true;

  configure(manifest: SceneManifest) {
    this.revision = manifest.sceneRevision;
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
    const snapshot = frame.type === 'stream.snapshot';
    const repeatedRecoverySnapshot =
      snapshot &&
      this.awaitingSnapshot &&
      frame.seq === this.lastSeq &&
      frame.epoch === this.epoch;
    if (
      frame.sceneRevision !== this.revision ||
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
    for (const pose of frame.poses) {
      const index = this.indices.get(pose[0]);
      if (index === undefined) continue;
      const values = pose.slice(1);
      const norm = Math.hypot(...values.slice(3));
      if (!values.every(Number.isFinite) || norm < 1e-12) {
        this.skippedNonFinite++;
        continue;
      }
      const start = index * 7;
      for (let i = 0; i < 7; i++)
        data[start + i] = i < 3 ? values[i] : values[i] / norm;
    }
    this.authoritative = {
      sample: frame.sampleTimeMs,
      simTime: frame.simTime,
      seq: frame.seq,
      data,
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
    if (index === undefined || !data) return;
    const start = index * 7;
    return {
      p: Array.from(data.slice(start, start + 3)) as Pose['p'],
      q: Array.from(data.slice(start + 3, start + 7)) as Pose['q'],
    };
  }
}
