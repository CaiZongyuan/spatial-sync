import { useEffect } from 'react';
import { useThree } from '@react-three/fiber';
import {
  Box3,
  InstancedMesh,
  Mesh,
  PerspectiveCamera,
  Sphere,
  Vector3,
} from 'three';

export interface CameraState {
  position: number[];
  target: number[];
  fov: number;
  near: number;
  far: number;
}
declare global {
  interface Window {
    __spatialRenderDebug?: {
      fitScene: () => CameraState;
      setCamera: (state: CameraState) => void;
      camera: () => CameraState;
      frameStats: () => {
        registeredBodies: number;
        registeredPhysicalNodes: number;
        appliedBodies: number;
        potentialVisualInstances: number;
        potentialTriangles: number;
        drawCalls: number;
        submittedTriangles: number;
        mode: string;
      };
    };
  }
}

export function SceneDiagnostics({
  counts,
}: {
  counts: () => {
    registeredBodies: number;
    registeredPhysicalNodes: number;
    appliedBodies: number;
  };
}) {
  const state = useThree();
  useEffect(() => {
    const camera = state.camera as PerspectiveCamera;
    const controls = () =>
      state.get().controls as unknown as {
        target?: Vector3;
        update?: () => void;
      };
    const readCamera = (): CameraState => ({
      position: camera.position.toArray(),
      target: controls()?.target?.toArray() ?? [0, 0, 0],
      fov: camera.fov,
      near: camera.near,
      far: camera.far,
    });
    const setCamera = (value: CameraState) => {
      camera.position.fromArray(value.position);
      camera.fov = value.fov;
      camera.near = value.near;
      camera.far = value.far;
      controls()?.target?.fromArray(value.target);
      camera.lookAt(new Vector3().fromArray(value.target));
      camera.updateProjectionMatrix();
      controls()?.update?.();
    };
    window.__spatialRenderDebug = {
      camera: readCamera,
      setCamera,
      fitScene: () => {
        const root = state.scene.getObjectByName('physicsRoot');
        if (!root) throw new Error('No physics root');
        root.updateWorldMatrix(true, true);
        const sphere = new Box3()
          .setFromObject(root)
          .getBoundingSphere(new Sphere());
        const distance = Math.max(
          1,
          (sphere.radius / Math.sin((camera.fov * Math.PI) / 360)) * 1.2,
        );
        const position = sphere.center
          .clone()
          .add(new Vector3(1, 1.1, 1.2).normalize().multiplyScalar(distance));
        setCamera({
          position: position.toArray(),
          target: sphere.center.toArray(),
          fov: camera.fov,
          near: 0.01,
          far: Math.max(100, distance * 5),
        });
        return readCamera();
      },
      frameStats: () => {
        let potentialVisualInstances = 0,
          potentialTriangles = 0;
        state.scene.traverseVisible((object) => {
          if (!(object instanceof Mesh)) return;
          const instances = object instanceof InstancedMesh ? object.count : 1;
          potentialVisualInstances += instances;
          potentialTriangles +=
            (Math.min(
              object.geometry.index?.count ??
                object.geometry.getAttribute('position')?.count ??
                0,
              object.geometry.drawRange.count,
            ) /
              3) *
            instances;
        });
        return {
          ...counts(),
          potentialVisualInstances,
          potentialTriangles,
          drawCalls: state.gl.info.render.calls,
          submittedTriangles: state.gl.info.render.triangles,
          mode: 'individual',
        };
      },
    };
    return () => {
      delete window.__spatialRenderDebug;
    };
  }, [state, counts]);
  return null;
}
