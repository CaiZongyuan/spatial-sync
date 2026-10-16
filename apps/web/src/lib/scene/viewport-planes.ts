import type { Plane } from 'three';

/** physicsRoot rotates Z-up physics into Three Y-up by -pi/2 about X. */
export function physicsViewportPlane({
  normal,
  constant,
}: Plane): [number, number, number, number] {
  return [normal.x, -normal.z, normal.y, constant];
}
