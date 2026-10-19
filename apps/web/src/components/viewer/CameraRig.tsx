import { useEffect, useMemo, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { Box3, OrthographicCamera, PerspectiveCamera, Vector3 } from 'three';

export type CameraPreset = 'perspective' | 'isometric';

export function CameraRig({ preset, bounds, revision }: { preset: CameraPreset; bounds: () => Box3; revision: number }) {
  const { set, size, invalidate } = useThree();
  const camera = useMemo(() => preset === 'isometric'
    ? new OrthographicCamera(-1, 1, 1, -1, .01, 1000)
    : new PerspectiveCamera(44, 1, .01, 1000), [preset]);
  const pending = useRef(true);
  useEffect(() => {
    set({ camera });
  }, [camera, set]);
  useEffect(() => {
    pending.current = true;
    invalidate();
  }, [camera, revision, size.width, size.height, invalidate]);
  useFrame(state => {
    if (!pending.current) return;
    pending.current = false;
    const box = bounds();
    if (box.isEmpty()) return;
    const dimensions = box.getSize(new Vector3());
    const center = box.getCenter(new Vector3());
    const radius = Math.max(.5, dimensions.length() / 2);
    const aspect = Math.max(.1, size.width / size.height);
    if (camera instanceof OrthographicCamera) {
      const half = radius * .9;
      camera.left = -half * aspect; camera.right = half * aspect;
      camera.top = half; camera.bottom = -half;
      if (aspect < 1) { camera.top /= aspect; camera.bottom /= aspect; }
      camera.position.copy(center).add(new Vector3(1, .8, 1).normalize().multiplyScalar(radius * 4));
    } else {
      camera.aspect = aspect;
      camera.position.copy(center).add(new Vector3(1, .85, 1.2).normalize().multiplyScalar(radius * 3.5 / Math.min(1, aspect)));
    }
    camera.lookAt(center); camera.updateProjectionMatrix();
    const controls = state.controls as unknown as { target?: Vector3; update?: () => void } | null;
    controls?.target?.copy(center); controls?.update?.();
    state.invalidate();
  });
  return null;
}
