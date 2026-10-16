import { useEffect, useMemo } from 'react';
import { useThree } from '@react-three/fiber';
import {
  Box3,
  Color,
  DoubleSide,
  DynamicDrawUsage,
  Frustum,
  InstancedMesh,
  Matrix4,
  MeshStandardMaterial,
  Quaternion,
  Sphere,
  Vector3,
} from 'three';
import type { SceneManifest, Primitive, Pose } from '../../lib/protocol';
import type { SessionClient } from '../../lib/stream/client';
import { createPrimitiveGeometry } from '../../lib/geometry/primitives';
import type { RenderedPose, VisualGeometryInfo } from './SceneViewport';

export type PhysicalNode = Exclude<
  SceneManifest['nodes'][number],
  { kind: 'group' }
>;
interface Entry {
  node: PhysicalNode;
  visual: Primitive;
  offset?: number;
  local: Matrix4;
  inverse: Matrix4;
  fixed?: Matrix4;
  first: boolean;
  batch: Batch;
  slot: number;
}
interface Batch {
  geometry: ReturnType<typeof createPrimitiveGeometry>;
  entries: Entry[];
  mesh?: InstancedMesh;
  packed: Entry[];
  bind: (mesh: InstancedMesh | null) => void;
  coarse?: boolean;
  low?: Batch;
}
export interface InstanceController {
  apply: (data: Float64Array) => void;
  inspect: (key: string) => VisualGeometryInfo | undefined;
  bounds: () => Box3;
  registeredBodies: number;
  registeredPhysicalNodes: number;
}

const identity = new Vector3(1, 1, 1);
function matrix(pose: Pose) {
  return new Matrix4().compose(
    new Vector3(...pose.p),
    new Quaternion(...pose.q),
    identity,
  );
}

export function canInstance(node: PhysicalNode, manifest: SceneManifest) {
  return (
    node.visuals.length > 0 &&
    node.visuals.every(
      (visual) =>
        visual.kind === 'primitive' && visual.geometry.type !== 'plane',
    ) &&
    !manifest.sites.some((site) => site.parentBodyKey === node.entityKey)
  );
}

/** One actual instance matrix owns each drawn primitive; membership packs live slots. */
export function InstancedPrimitives({
  nodes,
  client,
  selected,
  hidden,
  rendered,
  controller,
  select,
}: {
  nodes: PhysicalNode[];
  client: SessionClient;
  selected?: string;
  hidden: Set<string>;
  rendered: React.RefObject<Map<string, RenderedPose>>;
  controller: React.RefObject<InstanceController | undefined>;
  select: (key: string) => void;
}) {
  const camera = useThree((state) => state.camera);
  const size = useThree((state) => state.size);
  const adaptive =
    new URLSearchParams(location.search).get('quality') !== 'full';
  const batches = useMemo(() => {
    const groups = new Map<string, Batch>();
    const createBatch = (visual: Primitive, coarse = false): Batch => {
      const batch: Batch = {
        geometry: createPrimitiveGeometry(
          visual.geometry,
          coarse ? 'low' : 'full',
        ),
        entries: [],
        packed: [],
        bind: () => {},
        coarse,
      };
      batch.geometry.computeBoundingBox();
      batch.geometry.computeBoundingSphere();
      batch.bind = (mesh) => {
        batch.mesh = mesh ?? undefined;
        if (mesh) {
          mesh.count = 0;
          mesh.instanceMatrix.setUsage(DynamicDrawUsage);
          mesh.userData.instanceKey = (slot: number) =>
            batch.packed[slot]?.node.entityKey;
          mesh.userData.lod = coarse ? 'low' : 'full';
        }
      };
      return batch;
    };
    for (const node of nodes)
      for (const [index, visual] of node.visuals.entries()) {
        if (visual.kind !== 'primitive') continue;
        const signature = JSON.stringify(visual.geometry);
        let batch = groups.get(signature);
        if (!batch) {
          batch = createBatch(visual);
          if (visual.geometry.type !== 'box') {
            batch.low = createBatch(visual, true);
            batch.low.entries = batch.entries;
          }
          groups.set(signature, batch);
        }
        const local = matrix(visual.localPose);
        const offset =
          node.kind === 'body'
            ? client.buffer.indexForKey(node.entityKey)
            : undefined;
        batch.entries.push({
          node,
          visual,
          local,
          inverse: local.clone().invert(),
          first: index === 0,
          offset: offset === undefined ? undefined : offset * 7,
          fixed: node.kind === 'static' ? matrix(node.initialPose) : undefined,
          batch,
          slot: -1,
        });
      }
    return [...groups.values()].flatMap((batch) =>
      batch.low ? [batch, batch.low] : [batch],
    );
  }, [nodes, client]);
  const material = useMemo(
    () =>
      new MeshStandardMaterial({
        color: '#ffffff',
        roughness: 0.45,
        metalness: 0.24,
        side: DoubleSide,
      }),
    [],
  );
  useEffect(
    () => () => {
      batches.forEach((batch) => batch.geometry.dispose());
    },
    [batches],
  );
  useEffect(() => () => material.dispose(), [material]);
  useEffect(() => {
    const p = new Vector3(),
      q = new Quaternion(),
      scale = new Vector3(),
      value = new Matrix4(),
      read = new Matrix4(),
      world = new Matrix4(),
      projection = new Matrix4();
    const frustum = new Frustum(),
      sphere = new Sphere(),
      cameraPoint = new Vector3();
    const allBounds = new Box3(),
      visualBounds = new Box3();
    const base = new Color('#8dabb5'),
      highlight = new Color('#ffbf69');
    const slots = new Map<string, Entry[]>();
    const appliedAt = new Map<string, number>();
    let frame = 0;
    for (const batch of batches.filter((batch) => !batch.coarse))
      for (const entry of batch.entries) {
        const owned = slots.get(entry.node.entityKey) ?? [];
        owned.push(entry);
        slots.set(entry.node.entityKey, owned);
      }
    controller.current = {
      registeredBodies: nodes.filter((node) => node.kind === 'body').length,
      registeredPhysicalNodes: nodes.length,
      bounds: () => allBounds.clone(),
      apply: (data) => {
        frame++;
        camera.updateMatrixWorld(true);
        frustum.setFromProjectionMatrix(
          projection.multiplyMatrices(
            camera.projectionMatrix,
            camera.matrixWorldInverse,
          ),
        );
        allBounds.makeEmpty();
        for (const batch of batches) batch.packed.length = 0;
        for (const batch of batches) {
          if (batch.coarse || !batch.mesh) continue;
          batch.mesh.updateWorldMatrix(true, false);
          for (const entry of batch.entries) {
            entry.slot = -1;
            const key = entry.node.entityKey;
            if (hidden.has(key) || !client.isEntityVisible(key)) {
              if (entry.first && appliedAt.get(key) !== frame)
                rendered.current.delete(key);
              continue;
            }
            if (entry.fixed) value.copy(entry.fixed);
            else if (entry.offset !== undefined)
              value.compose(
                p.fromArray(data, entry.offset),
                q.fromArray(data, entry.offset + 3),
                identity,
              );
            else continue;
            value.multiply(entry.local);
            world.multiplyMatrices(batch.mesh.matrixWorld, value);
            allBounds.union(
              visualBounds
                .copy(batch.geometry.boundingBox!)
                .applyMatrix4(world),
            );
            sphere.copy(batch.geometry.boundingSphere!).applyMatrix4(world);
            // Bounds include VisualLocal and shape extents, so an origin outside
            // the view never causes a partly visible primitive to disappear.
            if (!frustum.intersectsSphere(sphere)) {
              if (entry.first && appliedAt.get(key) !== frame)
                rendered.current.delete(key);
              continue;
            }
            cameraPoint
              .copy(sphere.center)
              .applyMatrix4(camera.matrixWorldInverse);
            const diameterPx =
              (sphere.radius *
                size.height *
                camera.projectionMatrix.elements[5]) /
              Math.max(1e-6, -cameraPoint.z);
            const target =
              adaptive && selected !== key && diameterPx < 12 && batch.low
                ? batch.low
                : batch;
            const mesh = target.mesh;
            if (!mesh) continue;
            const slot = target.packed.length;
            entry.batch = target;
            entry.slot = slot;
            mesh.setMatrixAt(slot, value);
            mesh.setColorAt(slot, selected === key ? highlight : base);
            target.packed.push(entry);
            if (entry.node.kind === 'body' && appliedAt.get(key) !== frame) {
              // Read the Float32 matrix that will actually be submitted, then
              // remove VisualLocal to recover the protocol's world Body pose.
              mesh.getMatrixAt(slot, read);
              read.multiply(entry.inverse).decompose(p, q, scale);
              let receipt = rendered.current.get(key);
              if (!receipt) {
                receipt = { p: [0, 0, 0], q: [0, 0, 0, 1] };
                rendered.current.set(key, receipt);
              }
              p.toArray(receipt.p);
              q.normalize().toArray(receipt.q);
              appliedAt.set(key, frame);
            }
          }
        }
        for (const batch of batches) {
          const mesh = batch.mesh;
          if (!mesh) continue;
          mesh.count = batch.packed.length;
          mesh.visible = mesh.count > 0;
          mesh.instanceMatrix.needsUpdate = true;
          if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
          mesh.boundingBox = null;
          mesh.boundingSphere = null;
        }
      },
      inspect: (key) => {
        const owned = slots.get(key)?.filter((entry) => entry.slot >= 0);
        if (!owned?.length) return;
        const bounds = new Box3();
        for (const { batch, slot } of owned) {
          const mesh = batch.mesh!;
          mesh.updateWorldMatrix(true, false);
          mesh.getMatrixAt(slot, read);
          value.multiplyMatrices(mesh.matrixWorld, read);
          if (!batch.geometry.boundingBox) batch.geometry.computeBoundingBox();
          bounds.union(batch.geometry.boundingBox!.clone().applyMatrix4(value));
        }
        return {
          tier: 'visuals',
          visualCount: owned.length,
          meshCount: owned.length,
          normalsComplete: true,
          min: bounds.min.toArray(),
          max: bounds.max.toArray(),
        };
      },
    };
    return () => {
      controller.current = undefined;
    };
  }, [
    batches,
    client,
    hidden,
    selected,
    rendered,
    controller,
    nodes,
    camera,
    size.height,
    adaptive,
  ]);
  return (
    <>
      {batches.map((batch, index) => (
        <instancedMesh
          key={index}
          args={[batch.geometry, material, batch.entries.length]}
          ref={batch.bind}
          frustumCulled={false}
          dispose={null}
          onPointerDown={(event) => {
            const entry =
              event.instanceId === undefined
                ? undefined
                : batch.packed[event.instanceId];
            if (
              !entry ||
              hidden.has(entry.node.entityKey) ||
              !client.isEntityVisible(entry.node.entityKey)
            )
              return;
            event.stopPropagation();
            select(entry.node.entityKey);
          }}
        />
      ))}
    </>
  );
}
