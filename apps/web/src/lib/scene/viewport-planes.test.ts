import { expect, it } from 'vitest';
import { Plane, Vector3 } from 'three';
import { physicsViewportPlane } from './viewport-planes';

it('maps Three Y and -Z clipping bounds onto physics Z and Y without changing the half-space', () => {
  expect(physicsViewportPlane(new Plane(new Vector3(0, 1, 0), -2))).toEqual([
    0, -0, 1, -2,
  ]);
  expect(physicsViewportPlane(new Plane(new Vector3(0, 0, -1), -3))).toEqual([
    0, 1, 0, -3,
  ]);
  const plane = new Plane(new Vector3(1, 2, 3), 4);
  const physicsPoint = [2, 3, 4];
  // Independent -pi/2 X rotation: (x,y,z) -> (x,z,-y).
  const threePoint = new Vector3(2, 4, -3);
  const [nx, ny, nz, d] = physicsViewportPlane(plane);
  expect(
    nx * physicsPoint[0] + ny * physicsPoint[1] + nz * physicsPoint[2] + d,
  ).toBe(plane.distanceToPoint(threePoint));
});
