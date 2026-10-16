import {
  BoxGeometry,
  SphereGeometry,
  CapsuleGeometry,
  CylinderGeometry,
  ConeGeometry,
  PlaneGeometry,
  type BufferGeometry,
} from 'three';
import type { PrimitiveGeometry } from '../protocol';

/** Newton dimensions and Z axes are converted exactly once, before localPose. */
export function createPrimitiveGeometry(
  shape: PrimitiveGeometry,
): BufferGeometry {
  switch (shape.type) {
    case 'box':
      return new BoxGeometry(
        ...(shape.halfExtents.map((v) => v * 2) as [number, number, number]),
      );
    case 'sphere':
      return new SphereGeometry(shape.radius, 24, 16);
    case 'capsule':
      return new CapsuleGeometry(
        shape.radius,
        2 * shape.halfHeight,
        6,
        16,
      ).rotateX(Math.PI / 2);
    case 'cylinder':
      return new CylinderGeometry(
        shape.radius,
        shape.radius,
        2 * shape.halfHeight,
        24,
      ).rotateX(Math.PI / 2);
    case 'cone':
      return new ConeGeometry(
        shape.radius,
        2 * shape.halfHeight,
        24,
      ).rotateX(Math.PI / 2);
    case 'plane':
      return new PlaneGeometry(shape.width || 100, shape.length || 100);
  }
}
