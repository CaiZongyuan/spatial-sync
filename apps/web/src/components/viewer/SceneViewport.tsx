import { useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { CameraSubscription } from './CameraSubscription';
import {
  DoubleSide,
  Box3,
  Group,
  Mesh,
  MeshStandardMaterial,
  type Material,
} from 'three';
import {
  resolveVisualTiers,
  type LoadedVisual,
  type ResolvedVisuals,
  type VisualTier,
} from '../../lib/scene/visual-tiers';
import type { SceneManifest, Visual, Pose } from '../../lib/protocol';
import { IDENTITY } from '../../lib/scene/pose';
import { createPrimitiveGeometry } from '../../lib/geometry/primitives';
import type { SessionClient } from '../../lib/stream/client';
import type { FrameRecorder } from '../../lib/performance/frame-recorder';

export interface DebugLayers {
  colliders: boolean;
  sites: boolean;
  origins: boolean;
  grid: boolean;
}
export interface RenderMetrics {
  fps: number;
  drawCalls: number;
  objects: number;
  applyMs: number;
}
export interface RenderedPose {
  p: number[];
  q: number[];
}
export interface VisualGeometryInfo {
  tier: string;
  visualCount: number;
  meshCount: number;
  normalsComplete: boolean;
  min: number[];
  max: number[];
}
export type InspectVisuals = (key: string) => VisualGeometryInfo | undefined;

function Asset({
  visual,
  object,
  selected,
  collider,
}: {
  visual: Exclude<Visual, { kind: 'primitive' }>;
  object: Group;
  selected: boolean;
  collider: boolean;
}) {
  useEffect(() => {
    object?.traverse((child) => {
      if (!(child instanceof Mesh)) return;
      const materials: Material[] = Array.isArray(child.material)
        ? child.material
        : [child.material];
      materials.forEach((material) => {
        if ('wireframe' in material) material.wireframe = collider;
        material.transparent = collider;
        material.opacity = collider ? 0.7 : 1;
        if (material instanceof MeshStandardMaterial) {
          material.emissive.set(selected ? '#b86b16' : '#000000');
          material.emissiveIntensity = selected ? 0.4 : 0;
        }
      });
    });
  }, [object, selected, collider]);
  return (
    <group
      rotation={visual.assetUpAxis === 'Y' ? [Math.PI / 2, 0, 0] : [0, 0, 0]}
      scale={visual.scale ?? [1, 1, 1]}
    >
      <primitive object={object} dispose={null} />
    </group>
  );
}

function PrimitiveMesh({
  visual,
  selected,
  collider,
}: {
  visual: Extract<Visual, { kind: 'primitive' }>;
  selected: boolean;
  collider: boolean;
}) {
  const geometry = useMemo(
    () => createPrimitiveGeometry(visual.geometry),
    [visual.geometry],
  );
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <mesh geometry={geometry} castShadow receiveShadow>
      <meshStandardMaterial
        color={
          collider
            ? '#71e3be'
            : selected
              ? '#ffbf69'
              : visual.geometry.type === 'plane'
                ? '#3a4852'
                : '#8dabb5'
        }
        roughness={0.45}
        metalness={0.24}
        side={DoubleSide}
        wireframe={collider}
        transparent={collider}
        opacity={collider ? 0.8 : 1}
      />
    </mesh>
  );
}

function VisualObject({
  loaded,
  selected,
  collider = false,
}: {
  loaded: LoadedVisual;
  selected: boolean;
  collider?: boolean;
}) {
  const { visual, object } = loaded;
  const pose = visual.localPose ?? IDENTITY;
  return (
    <group position={pose.p} quaternion={pose.q}>
      {visual.kind === 'primitive' ? (
        <PrimitiveMesh
          visual={visual}
          selected={selected}
          collider={collider}
        />
      ) : (
        object && (
          <Asset
            visual={visual}
            object={object}
            selected={selected}
            collider={collider}
          />
        )
      )}
    </group>
  );
}

function Placeholder({ selected }: { selected: boolean }) {
  return (
    <mesh>
      <boxGeometry args={[0.2, 0.2, 0.2]} />
      <meshStandardMaterial
        color={selected ? '#ffc56b' : '#d977b8'}
        wireframe
      />
    </mesh>
  );
}

type PhysicalNode = Exclude<SceneManifest['nodes'][number], { kind: 'group' }>;

function useVisuals(tiers: VisualTier[], warn: (message: string) => void) {
  const [resolved, setResolved] = useState<ResolvedVisuals>();
  useEffect(() => {
    const controller = new AbortController();
    let owned: ResolvedVisuals | undefined;
    setResolved(undefined);
    void resolveVisualTiers(tiers, controller.signal, warn).then((result) => {
      owned = result;
      if (result) setResolved(result);
    });
    return () => {
      controller.abort();
      owned?.dispose();
    };
  }, [tiers, warn]);
  return resolved;
}

function CollisionVisuals({
  visuals,
  warn,
}: {
  visuals: Visual[];
  warn: (message: string) => void;
}) {
  const tiers = useMemo<VisualTier[]>(
    () => [{ name: 'colliders', visuals }],
    [visuals],
  );
  const resolved = useVisuals(tiers, warn);
  return (
    <group name="collider-debug-group">
      {resolved?.visuals.map((loaded, i) => (
        <VisualObject key={i} loaded={loaded} selected={false} collider />
      ))}
    </group>
  );
}

function NodeVisuals({
  node,
  selected,
  colliders,
  warn,
}: {
  node: PhysicalNode;
  selected: boolean;
  colliders: boolean;
  warn: (message: string) => void;
}) {
  const tiers = useMemo<VisualTier[]>(
    () => [
      { name: 'visuals', visuals: node.visuals },
      { name: 'fallbackVisuals', visuals: node.fallbackVisuals ?? [] },
      { name: 'colliders', visuals: node.colliders },
    ],
    [node],
  );
  const report = useMemo(
    () => (message: string) => warn(`${node.entityKey}: ${message}`),
    [node.entityKey, warn],
  );
  const resolved = useVisuals(tiers, report);
  return (
    <>
      <group
        name="visual-group"
        userData={{ tier: resolved?.tier ?? 'loading' }}
      >
        {resolved?.tier === 'placeholder' ? (
          <Placeholder selected={selected} />
        ) : (
          resolved?.visuals.map((loaded, i) => (
            <VisualObject key={i} loaded={loaded} selected={selected} />
          ))
        )}
      </group>
      {colliders &&
        resolved &&
        resolved.tier !== 'colliders' &&
        node.colliders.length > 0 && (
          <CollisionVisuals visuals={node.colliders} warn={report} />
        )}
    </>
  );
}

function Body({
  node,
  manifest,
  selected,
  layers,
  hidden,
  select,
  register,
  warn,
}: {
  node: PhysicalNode;
  manifest: SceneManifest;
  selected?: string;
  layers: DebugLayers;
  hidden: Set<string>;
  select: (key: string) => void;
  register: (key: string, id: number | undefined, object: Group | null) => void;
  warn: (message: string) => void;
}) {
  const ownSites = manifest.sites.filter(
    (site) => site.parentBodyKey === node.entityKey,
  );
  const isSelected = selected === node.entityKey;
  const pose: Pose = node.kind === 'static' ? node.initialPose : IDENTITY;
  return (
    <group
      ref={(object) =>
        register(
          node.entityKey,
          node.kind === 'body' ? node.streamId : undefined,
          object,
        )
      }
      position={pose.p}
      quaternion={pose.q}
      visible={!hidden.has(node.entityKey)}
      onPointerDown={(event) => {
        event.stopPropagation();
        select(node.entityKey);
      }}
    >
      <NodeVisuals
        node={node}
        selected={isSelected}
        colliders={layers.colliders}
        warn={warn}
      />
      {layers.origins && <axesHelper args={[0.18]} />}
      {ownSites.map((site) => (
        <SiteMarker
          key={site.entityKey}
          site={site}
          visible={
            (layers.sites || selected === site.entityKey) &&
            !hidden.has(site.entityKey)
          }
          selected={selected === site.entityKey}
          select={select}
        />
      ))}
    </group>
  );
}

function SiteMarker({
  site,
  visible,
  selected,
  select,
}: {
  site: SceneManifest['sites'][number];
  visible: boolean;
  selected: boolean;
  select: (key: string) => void;
}) {
  return (
    <group
      position={site.localPose.p}
      quaternion={site.localPose.q}
      visible={visible}
      onPointerDown={(event) => {
        event.stopPropagation();
        select(site.entityKey);
      }}
    >
      <axesHelper args={[selected ? 0.26 : 0.14]} />
      <mesh>
        <sphereGeometry args={[selected ? 0.045 : 0.025, 12, 8]} />
        <meshBasicMaterial
          color={selected ? '#ffd093' : '#78e4d1'}
          depthTest={false}
        />
      </mesh>
    </group>
  );
}

function SceneContents({
  manifest,
  client,
  selected,
  layers,
  hidden,
  select,
  warn,
  metrics,
  rendered,
  inspectVisuals,
  recorder,
}: ViewportProps) {
  const selectedEntity =
    manifest.joints.find((joint) => joint.entityKey === selected)
      ?.anchorSiteKey ?? selected;
  const bodies = useRef(new Map<number, Group>());
  const physicalNodes = useRef(new Map<string, Group>());
  const elapsed = useRef({ time: 0, frames: 0 });
  const register = (
    key: string,
    id: number | undefined,
    object: Group | null,
  ) => {
    if (object) physicalNodes.current.set(key, object);
    else physicalNodes.current.delete(key);
    if (id !== undefined) {
      if (object) bodies.current.set(id, object);
      else bodies.current.delete(id);
    }
  };
  useEffect(() => {
    inspectVisuals.current = (key) => {
      const group = physicalNodes.current
        .get(key)
        ?.children.find((child) => child.name === 'visual-group');
      if (
        !group ||
        group.userData.tier === 'loading' ||
        group.children.length === 0
      )
        return;
      group.updateWorldMatrix(true, true);
      const bounds = new Box3().setFromObject(group);
      let meshCount = 0;
      let normalsComplete = true;
      group.traverse((object) => {
        if (object instanceof Mesh) {
          meshCount++;
          normalsComplete &&= Boolean(object.geometry.getAttribute('normal'));
        }
      });
      return {
        tier: group.userData.tier,
        visualCount: group.children.length,
        meshCount,
        normalsComplete,
        min: bounds.min.toArray(),
        max: bounds.max.toArray(),
      };
    };
    return () => {
      inspectVisuals.current = undefined;
    };
  }, [inspectVisuals]);
  useFrame(({ gl, scene }, delta) => {
    const start = performance.now();
    const data = client.buffer.sample(start);
    for (const node of manifest.nodes) {
      if (node.kind !== 'body') continue;
      const object = bodies.current.get(node.streamId);
      const index = client.buffer.indexForKey(node.entityKey);
      if (!object || index === undefined) continue;
      const offset = index * 7;
      object.position.fromArray(data, offset);
      object.quaternion.fromArray(data, offset + 3);
      let receipt = rendered.current.get(node.entityKey);
      if (!receipt) {
        receipt = { p: [0, 0, 0], q: [0, 0, 0, 1] };
        rendered.current.set(node.entityKey, receipt);
      }
      object.position.toArray(receipt.p);
      object.quaternion.toArray(receipt.q);
    }
    metrics.current.applyMs = performance.now() - start;
    recorder.record(
      start,
      metrics.current.applyMs,
      gl.info.render.calls,
      metrics.current.objects,
      Math.max(0, start - client.buffer.receivedAt),
    );
    elapsed.current.time += delta;
    elapsed.current.frames++;
    if (elapsed.current.time >= 1) {
      metrics.current.fps = elapsed.current.frames / elapsed.current.time;
      metrics.current.drawCalls = gl.info.render.calls;
      let objects = 0;
      scene.traverse(() => objects++);
      metrics.current.objects = objects;
      elapsed.current = { time: 0, frames: 0 };
    }
  });
  return (
    <>
      <CameraSubscription client={client} />
      <color attach="background" args={['#172329']} />
      <ambientLight intensity={1.5} />
      <directionalLight position={[3, 6, 3]} intensity={3} />
      <directionalLight
        position={[-4, 2, -2]}
        intensity={1.2}
        color="#8bbfda"
      />
      {layers.grid && (
        <gridHelper
          args={[12, 24, '#48636a', '#2a3d44']}
          position={[0, -0.002, 0]}
        />
      )}
      <group name="physicsRoot" rotation={[-Math.PI / 2, 0, 0]}>
        <axesHelper args={[0.6]} />
        {manifest.nodes
          .filter((node): node is PhysicalNode => node.kind !== 'group')
          .map((node) => (
            <Body
              key={node.entityKey}
              {...{ node, manifest, layers, hidden, select, register, warn }}
              selected={selectedEntity}
            />
          ))}
        {manifest.sites
          .filter((site) => site.parentBodyKey == null)
          .map((site) => (
            <SiteMarker
              key={site.entityKey}
              site={site}
              visible={
                (layers.sites || selectedEntity === site.entityKey) &&
                !hidden.has(site.entityKey)
              }
              selected={selectedEntity === site.entityKey}
              select={select}
            />
          ))}
      </group>
      <OrbitControls
        makeDefault
        target={[0, 0.65, 0]}
        minDistance={0.3}
        maxDistance={30}
      />
    </>
  );
}

interface ViewportProps {
  manifest: SceneManifest;
  client: SessionClient;
  selected?: string;
  layers: DebugLayers;
  hidden: Set<string>;
  select: (key: string) => void;
  warn: (message: string) => void;
  metrics: React.RefObject<RenderMetrics>;
  rendered: React.RefObject<Map<string, RenderedPose>>;
  inspectVisuals: React.RefObject<InspectVisuals | undefined>;
  recorder: FrameRecorder;
}

export function SceneViewport(props: ViewportProps) {
  return (
    <div className="viewport" data-testid="scene-viewport">
      <Canvas
        camera={{ position: [4.2, 3, 4.8], fov: 44, near: 0.01, far: 100 }}
        dpr={[1, 1.75]}
        gl={{ antialias: true }}
        onPointerMissed={() => props.select('')}
      >
        <SceneContents key={props.manifest.sceneRevision} {...props} />
      </Canvas>
      <div className="viewport-label">
        <span className="live-dot" /> LIVE SPATIAL VIEW{' '}
        <small>METERS · Z-UP</small>
      </div>
      <div className="viewport-hint">
        Drag to orbit <span>·</span> Scroll to zoom <span>·</span> Click to
        inspect
      </div>
    </div>
  );
}
