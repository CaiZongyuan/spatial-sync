import { beforeEach, describe, expect, it } from 'vitest';
import manifestFixture from '../../../../../packages/protocol/fixtures/manifest.json';
import type { TransformFrame } from '../protocol';
import { validateManifest } from '../validate-manifest';
import { PoseBuffer } from './pose-buffer';

const manifest = validateManifest(manifestFixture);
const key = '/World/Arm';
const makeFrame = (
  seq: number,
  sample: number,
  x: number,
  overrides: Partial<TransformFrame> = {},
): TransformFrame => ({
  type: seq === 1 ? 'stream.snapshot' : 'transform.frame',
  protocolVersion: 1,
  sessionId: manifest.sessionId,
  sceneRevision: manifest.sceneRevision,
  epoch: 0,
  seq,
  simTime: seq / 240,
  sampleTimeMs: sample,
  poses: [[7, x, 0, 1, 0, 0, 0, 1]],
  ...overrides,
});

describe('sample-clock renderer buffer', () => {
  let buffer: PoseBuffer;
  beforeEach(() => {
    buffer = new PoseBuffer();
    buffer.configure(manifest);
  });

  it('uses the sliding minimum clock offset and two granted frame periods', () => {
    buffer.push(makeFrame(1, 100, 0), 1010);
    buffer.push(makeFrame(2, 133.333, 1), 1033.333);
    buffer.push(makeFrame(3, 166.666, 2), 1076.666);
    expect(buffer.clockOffset).toBeCloseTo(900);
    expect(buffer.frameCount).toBe(3);
    buffer.sample(1100);
    expect(buffer.read(key)!.p[0]).toBeCloseTo(1, 3);
    buffer.grantedRateHz = 20;
    expect(buffer.playoutDelay).toBe(100);
  });

  it('interpolates world position and same-hemisphere quaternion without using simTime as a clock', () => {
    buffer.push(makeFrame(1, 100, 0, { simTime: 0 }), 1000);
    buffer.push(
      makeFrame(2, 150, 10, {
        simTime: 500,
        poses: [[7, 10, 0, 1, 0, 0, 0, -1]],
      }),
      1050,
    );
    buffer.sample(1091.6666667);
    expect(buffer.read(key)!.p[0]).toBeCloseTo(5);
    expect(Math.abs(buffer.read(key)!.q[3])).toBeCloseTo(1);
    expect(buffer.readLatest(key)!.p[0]).toBe(10);
    expect(buffer.latestSample).toMatchObject({
      seq: 2,
      simTime: 500,
      sampleTimeMs: 150,
    });
  });

  it('holds the newest pose without extrapolating', () => {
    buffer.push(makeFrame(1, 10, 1), 100);
    buffer.push(makeFrame(2, 40, 2), 130);
    buffer.sample(99999);
    expect(buffer.read(key)!.p[0]).toBe(2);
  });

  it('jumps immediately across gaps greater than three frame periods', () => {
    buffer.push(makeFrame(1, 10, 1), 100);
    buffer.push(makeFrame(2, 1010, 50), 1100);
    expect(buffer.read(key)!.p[0]).toBe(50);
    expect(buffer.frameCount).toBe(1);
    expect(buffer.discontinuities).toBe(1);
    buffer.sample(1100);
    expect(buffer.read(key)!.p[0]).toBe(50);
  });

  it('shows paused step frames immediately, including closely spaced steps', () => {
    buffer.push(makeFrame(1, 10, 1), 100);
    buffer.paused = true;
    buffer.push(makeFrame(2, 30, 2), 120);
    buffer.sample(121);
    expect(buffer.read(key)!.p[0]).toBe(2);
  });

  it('resets on higher-epoch snapshot before state, discarding old timeline and duplicates', () => {
    buffer.push(makeFrame(1, 10, 90), 100);
    expect(buffer.push(makeFrame(2, 20, 95, { epoch: 1 }), 110)).toBe(false);
    expect(
      buffer.push(
        makeFrame(3, 30, 0, { type: 'stream.snapshot', epoch: 1, simTime: 0 }),
        120,
      ),
    ).toBe(true);
    expect(buffer.read(key)!.p[0]).toBe(0);
    expect(buffer.push(makeFrame(4, 40, 99, { epoch: 0 }), 130)).toBe(false);
    expect(buffer.push(makeFrame(3, 30, 99, { epoch: 1 }), 130)).toBe(false);
    expect(buffer.epoch).toBe(1);
    expect(buffer.latestSample!.simTime).toBe(0);
  });

  it('rejects other revisions and only starts an epoch with a full snapshot', () => {
    expect(buffer.push(makeFrame(2, 20, 2), 110)).toBe(false);
    expect(
      buffer.push(makeFrame(1, 10, 1, { sceneRevision: 'old' }), 100),
    ).toBe(false);
    expect(buffer.push(makeFrame(1, 10, 1), 100)).toBe(true);
  });

  it('resynchronizes a paused equal-seq snapshot only in a recovery window', () => {
    buffer.push(makeFrame(1, 10, 1), 100);
    buffer.paused = true;
    buffer.expectSnapshot();
    expect(buffer.frameCount).toBe(0);
    expect(buffer.clockOffset).toBe(0);
    expect(buffer.push(makeFrame(1, 10, 1), 5100)).toBe(true);
    expect(buffer.awaitingSnapshot).toBe(false);
    expect(buffer.read(key)!.p[0]).toBe(1);
    expect(buffer.clockOffset).toBe(5090);
    expect(
      buffer.push(makeFrame(1, 10, 2, { type: 'transform.frame' }), 5101),
    ).toBe(false);
    expect(buffer.push(makeFrame(1, 10, 2), 5102)).toBe(false);
  });

  it('expires clock offsets after two seconds when a fresh frame arrives', () => {
    buffer.push(makeFrame(1, 10, 1), 100);
    buffer.push(makeFrame(2, 2010, 2), 2210);
    expect(buffer.clockOffset).toBe(200);
  });

  it('retains the previous valid pose for NaN or zero quaternion and counts it', () => {
    buffer.push(makeFrame(1, 10, 1), 100);
    buffer.push(
      makeFrame(2, 40, 2, { poses: [[7, NaN, 0, 1, 0, 0, 0, 1]] }),
      130,
    );
    buffer.push(
      makeFrame(3, 70, 3, { poses: [[7, 3, 0, 1, 0, 0, 0, 0]] }),
      160,
    );
    buffer.sample(1000);
    expect(buffer.read(key)!.p[0]).toBe(1);
    expect(buffer.skippedNonFinite).toBe(2);
  });
});

describe('cumulative scoped deltas and membership', () => {
  const scopeFrame = (
    seq: number,
    poses: TransformFrame['poses'],
    extra = {},
  ) =>
    makeFrame(seq, seq * 20, 0, {
      subscriptionId: 3,
      keyframe: true,
      poses,
      ...extra,
    });
  const delta = (
    seq: number,
    baseSeq: number,
    poses: TransformFrame['poses'],
    removedStreamIds: number[] = [],
  ) => ({
    ...scopeFrame(seq, poses),
    type: 'transform.delta' as const,
    baseSeq,
    subscriptionId: 3,
    removedStreamIds,
  });
  it('removes a non-origin pose instead of labeling held data with a new seq, then snaps re-entry', () => {
    const buffer = new PoseBuffer();
    buffer.configure(manifest);
    buffer.acceptSubscription(3);
    buffer.push(
      scopeFrame(10, [[7, 5, 6, 7, 0, 0, 0, 1]], { type: 'stream.snapshot' }),
      1000,
    );
    expect(buffer.readLatest(key)!.p).toEqual([5, 6, 7]);
    const revision = buffer.membershipRevision;
    buffer.push(delta(11, 10, [], [7]), 1020);
    expect(buffer.latestSample!.seq).toBe(11);
    expect(buffer.isMember(7)).toBe(false);
    expect(buffer.readLatest(key)).toBeUndefined();
    expect(buffer.read(key)).toBeUndefined();
    expect(buffer.membershipRevision).toBe(revision + 1);
    buffer.push(delta(12, 10, [[7, 9, 8, 7, 0, 0, 0, 1]]), 1040);
    buffer.sample(1041);
    expect(buffer.read(key)!.p).toEqual([9, 8, 7]);
  });
  it('reconstructs after a dropped intermediate delta and restores unchanged baseline values', () => {
    const buffer = new PoseBuffer();
    buffer.configure(manifest);
    buffer.acceptSubscription(3);
    buffer.push(
      scopeFrame(10, [[7, 5, 6, 7, 0, 0, 0, 1]], { type: 'stream.snapshot' }),
      1000,
    );
    // Delta 11 is lost on the latest-frame transport. Delta 12 remains cumulative.
    buffer.push(delta(12, 10, [[7, 9, 8, 7, 0, 0, 0, 1]]), 1040);
    expect(buffer.readLatest(key)!.p).toEqual([9, 8, 7]);
    buffer.push(delta(13, 10, []), 1060);
    expect(buffer.readLatest(key)!.p).toEqual([5, 6, 7]);
    expect(buffer.baseSeq).toBe(10);
  });
  it('preserves interpolation on regular keyframes and requests recovery for an unknown base', () => {
    const buffer = new PoseBuffer();
    buffer.configure(manifest);
    buffer.acceptSubscription(3);
    buffer.push(
      scopeFrame(10, [[7, 5, 6, 7, 0, 0, 0, 1]], { type: 'stream.snapshot' }),
      1000,
    );
    buffer.push(delta(11, 10, []), 1020);
    buffer.push(scopeFrame(12, [[7, 6, 6, 7, 0, 0, 0, 1]]), 1040);
    expect(buffer.frameCount).toBe(3);
    expect(buffer.baseSeq).toBe(12);
    expect(buffer.push(delta(13, 10, []), 1060)).toBe(false);
    expect(buffer.needsResync).toBe(true);
    expect(buffer.readLatest(key)!.p[0]).toBe(6);
    expect(buffer.push(scopeFrame(14, [], { subscriptionId: 2 }), 1080)).toBe(
      false,
    );
    expect(buffer.push(delta(14, 12, [], [7]), 1080)).toBe(true);
  });
  it('holds the last valid delta pose when a later delta contains an invalid component', () => {
    const buffer = new PoseBuffer();
    buffer.configure(manifest);
    buffer.acceptSubscription(3);
    buffer.push(
      scopeFrame(10, [[7, 0, 0, 0, 0, 0, 0, 1]], { type: 'stream.snapshot' }),
      1000,
    );
    buffer.push(delta(11, 10, [[7, 5, 0, 0, 0, 0, 0, 1]]), 1020);
    buffer.push(delta(12, 10, [[7, NaN, 0, 0, 0, 0, 0, 1]]), 1040);
    expect(buffer.readLatest(key)!.p[0]).toBe(5);
    expect(buffer.skippedNonFinite).toBe(1);
    buffer.push(delta(13, 10, []), 1060);
    expect(buffer.readLatest(key)!.p[0]).toBe(0);
  });
});
