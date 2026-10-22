import { Box3, OrthographicCamera, PerspectiveCamera, Vector3 } from 'three';

// View limits for the laboratory scene UI, not limits on protocol coordinates.
export const SCENE_CAMERA_DISTANCE = 100;
export const SCENE_FRAMING_RADIUS = 20;
export const defaultFraming = () => new Box3(new Vector3(-1, 0, -1), new Vector3(1, 2, 1));
export const finiteBounds = (box: Box3) => !box.isEmpty()
  && [...box.min.toArray(), ...box.max.toArray(), ...box.getCenter(new Vector3()).toArray(),
    box.getSize(new Vector3()).length()].every(Number.isFinite);

function median(values: number[]) {
  const sorted = values.slice().sort((a, b) => a - b);
  return (sorted[Math.floor((sorted.length - 1) / 2)] + sorted[Math.floor(sorted.length / 2)]) / 2;
}

/** Each entity gets one vote; remote geometry remains rendered at its true pose. */
export function sceneFramingBounds(boxes: Box3[], previous?: Box3): Box3 {
  const candidates = boxes.filter(box => finiteBounds(box)
    && box.getSize(new Vector3()).length() <= SCENE_FRAMING_RADIUS * 2);
  if (!candidates.length) return previous?.clone() ?? defaultFraming();
  const centers = candidates.map(box => box.getCenter(new Vector3()));
  const middle = new Vector3(median(centers.map(center => center.x)), median(centers.map(center => center.y)),
    median(centers.map(center => center.z)));
  const reference = previous?.getCenter(new Vector3()) ?? new Vector3();
  const anchor = centers.slice().sort((a, b) => {
    const difference = a.distanceToSquared(middle) - b.distanceToSquared(middle);
    return Math.abs(difference) > 1e-6 ? difference :
      a.distanceToSquared(reference) - b.distanceToSquared(reference) || a.x - b.x || a.y - b.y || a.z - b.z;
  })[0];
  const distances = centers.map(center => center.distanceTo(anchor)).sort((a, b) => a - b);
  const extent = median(candidates.map(box => box.getSize(new Vector3()).length()));
  const reach = Math.min(SCENE_FRAMING_RADIUS * 2,
    Math.max(10, extent * 6, distances[Math.floor((distances.length - 1) / 2)] * 6));
  const result = new Box3();
  candidates.forEach((box, index) => {
    if (centers[index].distanceTo(anchor) <= reach) result.union(box);
  });
  return result;
}

/** A viewport owns its initial fit until the person takes control. */
export class FramingState {
  private pending: 'initial' | 'manual' | undefined = 'initial';
  claimView() { this.pending = undefined; }
  requestFit() { this.pending = 'manual'; }
  takeFit(ready: () => boolean) {
    if (!this.pending || (this.pending === 'initial' && !ready())) return false;
    this.pending = undefined;
    return true;
  }
}

export function updateProjection(camera: PerspectiveCamera | OrthographicCamera, aspect: number, half: number) {
  if (camera instanceof PerspectiveCamera) camera.aspect = aspect;
  else {
    camera.left = -half * Math.max(1, aspect); camera.right = -camera.left;
    camera.top = half / Math.min(1, aspect); camera.bottom = -camera.top;
  }
  camera.updateProjectionMatrix();
}

export interface CameraFitPolicy { maxRadius?: number; maxDistance?: number; relativeScale?: boolean }

export function fitCamera(camera: PerspectiveCamera | OrthographicCamera, box: Box3, aspect: number,
  { maxRadius = Infinity, maxDistance = Infinity, relativeScale = false }: CameraFitPolicy = {}) {
  const safe = finiteBounds(box) ? box : defaultFraming();
  const center = safe.getCenter(new Vector3());
  const radius = Math.min(maxRadius, Math.max(relativeScale ? Number.EPSILON : .5,
    safe.getSize(new Vector3()).length() / 2));
  const direction = camera instanceof OrthographicCamera ? new Vector3(1, .8, 1) : new Vector3(1, .85, 1.2);
  const distance = Math.min(maxDistance, radius * (camera instanceof OrthographicCamera ? 4 :
    1.15 / Math.sin(Math.atan(Math.tan(camera.fov * Math.PI / 360) * Math.min(1, aspect)))));
  camera.position.copy(center).add(direction.normalize().multiplyScalar(distance));
  camera.zoom = 1;
  camera.near = relativeScale ? radius / 1000 : Math.max(.001, Math.min(.01, radius / 100));
  camera.far = relativeScale ? distance + radius * 4 : Math.max(1000, distance + radius * 4);
  camera.lookAt(center);
  const half = radius * 1.15;
  updateProjection(camera, aspect, half);
  return { center, half };
}
