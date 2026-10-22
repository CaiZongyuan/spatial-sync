import { useEffect, useMemo, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { Box3, OrthographicCamera, PerspectiveCamera, Vector3 } from 'three';
import { fitCamera, FramingState, updateProjection } from '../../lib/scene/camera-framing';

export type CameraPreset = 'perspective' | 'isometric';
const alwaysReady = () => true;
interface CameraControls {
  target?: Vector3;
  update?: () => void;
  addEventListener: (type: 'start', listener: () => void) => void;
  removeEventListener: (type: 'start', listener: () => void) => void;
}

export function CameraRig({ preset, bounds, revision, ready = alwaysReady, fitRequest = 0, onFit,
  maxRadius, maxDistance }: { preset: CameraPreset; bounds: () => Box3; revision: number;
    ready?: () => boolean; fitRequest?: number; onFit?: () => void; maxRadius?: number; maxDistance?: number }) {
  const { set, size, invalidate, controls } = useThree();
  const navigation = controls as unknown as CameraControls | null;
  const camera = useMemo(() => {
    const value = preset === 'isometric'
      ? new OrthographicCamera(-1, 1, 1, -1, .01, 1000)
      : new PerspectiveCamera(44, 1, .01, 1000);
    value.position.set(4.2, 3, 4.8);
    value.lookAt(0, .65, 0);
    return value;
  }, [preset]);
  const framing = useMemo(() => new FramingState(), [camera]);
  const half = useRef(1);
  const lastRequest = useRef(fitRequest);
  useEffect(() => {
    set({ camera });
  }, [camera, set]);
  useEffect(() => {
    updateProjection(camera, Math.max(.1, size.width / Math.max(1, size.height)), half.current);
    invalidate();
  }, [camera, size.width, size.height, invalidate]);
  useEffect(() => { invalidate(); }, [revision, ready, invalidate]);
  useEffect(() => {
    if (fitRequest === lastRequest.current) return;
    lastRequest.current = fitRequest;
    framing.requestFit(); invalidate();
  }, [fitRequest, framing, invalidate]);
  useEffect(() => {
    const start = () => framing.claimView();
    navigation?.addEventListener('start', start);
    return () => navigation?.removeEventListener('start', start);
  }, [navigation, framing]);
  useFrame(state => {
    if (!framing.takeFit(ready)) return;
    const fitted = fitCamera(camera, bounds(), Math.max(.1, size.width / Math.max(1, size.height)), maxRadius, maxDistance);
    half.current = fitted.half;
    const controls = state.controls as unknown as CameraControls | null;
    controls?.target?.copy(fitted.center); controls?.update?.();
    onFit?.();
    state.invalidate();
  });
  return null;
}
