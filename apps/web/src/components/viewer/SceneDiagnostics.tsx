import { useEffect } from 'react';
import { useThree } from '@react-three/fiber';
import {
  Box3,
  InstancedMesh,
  Mesh,
  Matrix4,
  Object3D,
  PerspectiveCamera,
  Raycaster,
  Sphere,
  Vector2,
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
      drawnEntities: () => string[];
      projectEntity: (
        key: string,
      ) => { x: number; y: number; instanceId?: number } | undefined;
      pick: (x: number, y: number) => { key: string; instanceId?: number }[];
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
  mode,
  extraBounds,
}: {
  mode: string;
  counts: () => {
    registeredBodies: number;
    registeredPhysicalNodes: number;
    appliedBodies: number;
  };
  extraBounds?: () => Box3 | undefined;
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
      state.invalidate();
    };
    const entityKey = (object: Object3D, slot?: number): string | undefined => {
      if (object instanceof InstancedMesh)
        return slot === undefined
          ? undefined
          : object.userData.instanceKey?.(slot);
      for (
        let current: Object3D | null = object;
        current;
        current = current.parent
      )
        if (current.userData.entityKey) return current.userData.entityKey;
    };
    const visibleMeshes = () => {
      state.scene.updateMatrixWorld(true);
      const meshes: Mesh[] = [];
      state.scene.traverseVisible((object) => {
        if (object instanceof Mesh) meshes.push(object);
      });
      return meshes;
    };
    window.__spatialRenderDebug = {
      camera: readCamera,
      setCamera,
      drawnEntities: () => {
        const keys = new Set<string>();
        for (const mesh of visibleMeshes()) {
          if (mesh instanceof InstancedMesh) {
            for (let slot = 0; slot < mesh.count; slot++) {
              const key = entityKey(mesh, slot);
              if (key) keys.add(key);
            }
          } else {
            const key = entityKey(mesh);
            if (key) keys.add(key);
          }
        }
        return [...keys];
      },
      projectEntity: (key) => {
        for (const mesh of visibleMeshes()) {
          const count = mesh instanceof InstancedMesh ? mesh.count : 1;
          for (let slot = 0; slot < count; slot++) {
            if (entityKey(mesh, slot) !== key) continue;
            if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
            const center = mesh.geometry.boundingBox!.getCenter(new Vector3());
            if (mesh instanceof InstancedMesh) {
              const instance = new Matrix4();
              mesh.getMatrixAt(slot, instance);
              center.applyMatrix4(instance);
            }
            center.applyMatrix4(mesh.matrixWorld).project(camera);
            const rect = state.gl.domElement.getBoundingClientRect();
            return {
              x: rect.left + ((center.x + 1) * rect.width) / 2,
              y: rect.top + ((1 - center.y) * rect.height) / 2,
              ...(mesh instanceof InstancedMesh ? { instanceId: slot } : {}),
            };
          }
        }
      },
      pick: (x, y) => {
        const rect = state.gl.domElement.getBoundingClientRect();
        const ray = new Raycaster();
        ray.setFromCamera(
          new Vector2(
            ((x - rect.left) / rect.width) * 2 - 1,
            1 - ((y - rect.top) / rect.height) * 2,
          ),
          camera,
        );
        return ray.intersectObjects(visibleMeshes(), false).flatMap((hit) => {
          const key = entityKey(hit.object, hit.instanceId);
          return key ? [{ key, instanceId: hit.instanceId }] : [];
        });
      },
      fitScene: () => {
        const root = state.scene.getObjectByName('physicsRoot');
        if (!root) throw new Error('No physics root');
        root.updateWorldMatrix(true, true);
        const bounds = new Box3().setFromObject(root);
        const potential = extraBounds?.();
        if (potential) bounds.union(potential);
        const sphere = bounds.getBoundingSphere(new Sphere());
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
          mode,
        };
      },
    };
    return () => {
      delete window.__spatialRenderDebug;
    };
  }, [state, counts, mode, extraBounds]);
  return null;
}
