import { describe, expect, it } from 'vitest';
import { Vector3 } from 'three';
import { createPrimitiveGeometry } from './primitives';
import type { PrimitiveGeometry as Primitive } from '../protocol';
import { multiplyPose } from '../scene/pose';
import shared from '../../../../../packages/protocol/fixtures/math.json';

const bounds = (shape: Primitive) => {
  const geometry = createPrimitiveGeometry(shape);
  geometry.computeBoundingBox();
  const size = geometry.boundingBox!.getSize(new Vector3()).toArray();
  geometry.dispose();
  return size;
};

describe('Newton geometry conventions', () => {
  it.each([
    [{ type: 'box', halfExtents: [1, 2, 3] }, [2, 4, 6]],
    [{ type: 'sphere', radius: 2 }, [4, 4, 4]],
    [{ type: 'capsule', radius: 1, halfHeight: 2 }, [2, 2, 6]],
    [{ type: 'cylinder', radius: 1, halfHeight: 2 }, [2, 2, 4]],
    [{ type: 'cone', radius: 1, halfHeight: 2 }, [2, 2, 4]],
    [{ type: 'plane', width: 3, length: 5 }, [3, 5, 0]],
    [{ type: 'plane', width: 0, length: 0 }, [100, 100, 0]],
  ] as [Primitive, number[]][])(
    '%j has Newton dimensions and axes',
    (shape, expected) => {
      bounds(shape).forEach((value, i) =>
        expect(value).toBeCloseTo(expected[i], 5),
      );
    },
  );

  it('puts the cone tip at positive Z and the base at negative Z', () => {
    const geometry = createPrimitiveGeometry({
      type: 'cone',
      radius: 1,
      halfHeight: 2,
    });
    const positions = geometry.getAttribute('position');
    let baseRadius = 0;
    for (let i = 0; i < positions.count; i++) {
      const radial = Math.hypot(positions.getX(i), positions.getY(i));
      if (positions.getZ(i) > 1.99) expect(radial).toBeLessThan(1e-5);
      if (positions.getZ(i) < -1.99) baseRadius = Math.max(baseRadius, radial);
    }
    expect(baseRadius).toBeCloseTo(1);
    geometry.dispose();
  });

  it('maps physics +Z to Three +Y once', () => {
    const z = new Vector3(0, 0, 1).applyAxisAngle(
      new Vector3(1, 0, 0),
      -Math.PI / 2,
    );
    expect(z.y).toBeCloseTo(1);
    expect(z.z).toBeCloseTo(0);
  });

  it('composes Body × VisualLocal and Site poses with the body rotation', () => {
    const pose = multiplyPose(
      { p: [1, 2, 3], q: [0, 0, Math.SQRT1_2, Math.SQRT1_2] },
      { p: [1, 0, 0], q: [0, 0, 0, 1] },
    );
    expect(pose.p[0]).toBeCloseTo(1);
    expect(pose.p[1]).toBeCloseTo(3);
    expect(pose.p[2]).toBeCloseTo(3);
  });

  it('consumes the Python-generated shared primitive and pose cases', () => {
    for (const item of shared.primitiveCases) {
      const shape = item.geometry as Primitive;
      const actual = bounds(shape);
      if ('size' in item.expected && item.expected.size)
        item.expected.size.forEach((value, i) =>
          expect(actual[i]).toBeCloseTo(value, 5),
        );
      if ('height' in item.expected && item.expected.height) {
        const expectedZ =
          item.expected.height +
          (shape.type === 'capsule' ? shape.radius * 2 : 0);
        expect(actual[2]).toBeCloseTo(expectedZ, 5);
      }
    }
    for (const item of shared.poseCases) {
      const result = multiplyPose(
        item.body as Parameters<typeof multiplyPose>[0],
        item.local as Parameters<typeof multiplyPose>[1],
      );
      result.p.forEach((value, i) =>
        expect(value).toBeCloseTo(item.expected.p[i], 6),
      );
      result.q.forEach((value, i) =>
        expect(value).toBeCloseTo(item.expected.q[i], 6),
      );
    }
  });
});
