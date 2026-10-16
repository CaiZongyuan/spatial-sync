import { Vector3, Quaternion } from 'three';
import type { SceneManifest, Pose } from '../protocol';
import { multiplyPose } from './pose';

export function jointPositionError(
  joint: SceneManifest['joints'][number],
  parent: Pose | undefined,
  childAnchor: Pose | undefined,
): number | undefined {
  if (
    !joint.parentLocalPose ||
    !childAnchor ||
    !['fixed', 'revolute', 'prismatic', 'ball'].includes(joint.type)
  )
    return;
  const anchor =
    joint.parentBodyKey == null
      ? joint.parentLocalPose
      : parent
        ? multiplyPose(parent, joint.parentLocalPose)
        : undefined;
  if (!anchor) return;
  const delta = new Vector3(...childAnchor.p).sub(new Vector3(...anchor.p));
  if (joint.type === 'prismatic' && joint.axis) {
    delta.applyQuaternion(new Quaternion(...anchor.q).invert());
    const axis = new Vector3(...joint.axis).normalize();
    delta.addScaledVector(axis, -delta.dot(axis));
  }
  return delta.length();
}
