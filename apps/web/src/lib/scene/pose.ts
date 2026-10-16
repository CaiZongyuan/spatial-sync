import { Quaternion, Vector3 } from 'three';
import type { Pose } from '../protocol';

export const IDENTITY: Pose = { p: [0, 0, 0], q: [0, 0, 0, 1] };

export function multiplyPose(parent: Pose, local: Pose): Pose {
  const q = new Quaternion(...parent.q).normalize();
  const p = new Vector3(...local.p)
    .applyQuaternion(q)
    .add(new Vector3(...parent.p));
  q.multiply(new Quaternion(...local.q).normalize());
  return { p: p.toArray(), q: q.toArray() };
}
