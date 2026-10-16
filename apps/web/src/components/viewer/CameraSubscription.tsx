import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Frustum, Matrix4 } from 'three';
import type { SessionClient } from '../../lib/stream/client';
import { physicsViewportPlane } from '../../lib/scene/viewport-planes';

/** Three's Y-up frustum is converted into the protocol's physics Z-up world. */
export function CameraSubscription({ client }: { client: SessionClient }) {
  const state = useRef({
    matrix: new Matrix4(),
    frustum: new Frustum(),
    last: '',
    at: -Infinity,
    active: false,
  });
  useFrame(({ camera }) => {
    const value = state.current;
    if (!client.usesViewport()) {
      value.active = false;
      return;
    }
    const now = performance.now();
    if (value.active && now - value.at < 100) return;
    camera.updateMatrixWorld();
    value.matrix.multiplyMatrices(
      camera.projectionMatrix,
      camera.matrixWorldInverse,
    );
    value.frustum.setFromProjectionMatrix(value.matrix);
    const planes = value.frustum.planes.map(physicsViewportPlane);
    const signature = planes
      .flat()
      .map((value) => value.toFixed(5))
      .join(',');
    if (!value.active || signature !== value.last) {
      client.updateViewport(planes);
      value.last = signature;
      value.at = now;
    }
    value.active = true;
  });
  return null;
}
