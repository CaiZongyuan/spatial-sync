import { describe, expect, it } from 'vitest';
import type { Joint, Pose } from '../protocol';
import { jointPositionError } from './joint-error';

const identity: Pose = { p: [0, 0, 0], q: [0, 0, 0, 1] };
const joint: Joint = {
  entityKey: '/Joint',
  type: 'prismatic',
  childBodyKey: '/Body',
  parentLocalPose: identity,
  axis: [0, 0, 1],
  dofCount: 1,
  stableKey: true,
};

describe('joint positional error removes legal degrees of freedom', () => {
  it('subtracts prismatic axis motion and retains transverse error', () => {
    expect(
      jointPositionError(joint, undefined, {
        p: [0.03, 0.04, 12],
        q: identity.q,
      }),
    ).toBeCloseTo(0.05);
  });
  it('interprets the prismatic axis in parent anchor coordinates', () => {
    const rotated = {
      ...joint,
      parentLocalPose: {
        p: identity.p,
        q: [0, Math.SQRT1_2, 0, Math.SQRT1_2] as Pose['q'],
      },
    };
    expect(
      jointPositionError(rotated, undefined, {
        p: [12, 0.04, 0.03],
        q: identity.q,
      }),
    ).toBeCloseTo(0.05);
  });
  it.each(['fixed', 'revolute', 'ball'])(
    '%s reports full anchor separation',
    (type) => {
      expect(
        jointPositionError({ ...joint, type }, undefined, {
          p: [0, 3, 4],
          q: identity.q,
        }),
      ).toBe(5);
    },
  );
  it.each(['free', 'd6', 'distance'])(
    '%s is marked unsupported rather than producing a false error',
    (type) => {
      expect(
        jointPositionError({ ...joint, type }, undefined, identity),
      ).toBeUndefined();
    },
  );
});
