import { useContext, useEffect, useMemo } from 'react';
import {
  Box3,
  Color,
  DoubleSide,
  DynamicDrawUsage,
  InstancedMesh,
  Matrix4,
  MeshStandardMaterial,
  Quaternion,
  Vector3,
} from 'three';
import type { SceneManifest, Primitive, Pose } from '../../lib/protocol';
import type { SessionClient } from '../../lib/stream/client';
import { createPrimitiveGeometry } from '../../lib/geometry/primitives';
import { ViewerThemeContext } from '../../lib/scene/viewer-theme';
import type { RenderedPose, VisualGeometryInfo } from './SceneViewport';

export type PhysicalNode = Exclude<
  SceneManifest['nodes'][number],
  { kind: 'group' }
>;
interface Entry {
  node: PhysicalNode;
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

/** A packed instance matrix owns each visible primitive and its picking identity. */
export function InstancedPrimitives({
  nodes,
  client,
  selected,
  highlightedKeys,
  hidden,
  rendered,
  controller,
  select,
}: {
  nodes: PhysicalNode[];
  client: SessionClient;
  selected?: string;
  highlightedKeys?: Set<string>;
  hidden: Set<string>;
  rendered: React.RefObject<Map<string, RenderedPose>>;
  controller: React.RefObject<InstanceController | undefined>;
  select: (key: string) => void;
}) {
  const theme = useContext(ViewerThemeContext);
  const batches = useMemo(() => {
    const groups = new Map<string, Batch>();
    const createBatch = (visual: Primitive): Batch => {
      const batch: Batch = {
        geometry: createPrimitiveGeometry(visual.geometry),
        entries: [],
        packed: [],
        bind: () => {},
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
          groups.set(signature, batch);
        }
        const local = matrix(visual.localPose);
        const offset =
          node.kind === 'body'
            ? client.buffer.indexForKey(node.entityKey)
            : undefined;
        batch.entries.push({
          node,
          local,
          inverse: local.clone().invert(),
          first: index === 0,
          offset: offset === undefined ? undefined : offset * 7,
          fixed: node.kind === 'static' ? matrix(node.initialPose) : undefined,
          batch,
          slot: -1,
        });
      }
    return [...groups.values()];
  }, [nodes, client]);
  const material = useMemo(
    () =>
      new MeshStandardMaterial({
        color: '#ffffff',
        roughness: 0.7,
        metalness: 0.12,
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
      world = new Matrix4();
    const allBounds = new Box3(),
      visualBounds = new Box3();
    const base = new Color(theme.body),
      highlight = new Color(theme.accent);
    const slots = new Map<string, Entry[]>();
    const appliedAt = new Map<string, number>();
    let frame = 0;
    for (const batch of batches)
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
        allBounds.makeEmpty();
        for (const batch of batches) batch.packed.length = 0;
        for (const batch of batches) {
          if (!batch.mesh) continue;
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
            const mesh = batch.mesh;
            const slot = batch.packed.length;
            entry.slot = slot;
            mesh.setMatrixAt(slot, value);
            mesh.setColorAt(slot, selected === key || highlightedKeys?.has(key) ? highlight : base);
            batch.packed.push(entry);
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
          source: 'visuals',
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
  }, [batches, client, hidden, selected, rendered, controller, nodes, theme, highlightedKeys]);
  return (
    <>
      {batches.map((batch, index) => (
        <instancedMesh
          key={index}
          args={[batch.geometry, material, batch.entries.length]}
          ref={batch.bind}
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
