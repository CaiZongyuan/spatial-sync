import { useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import {
  DoubleSide,
  Group,
  Mesh,
  MeshStandardMaterial,
  Texture,
  type Material,
  type Object3D,
} from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { SceneManifest, Visual, Pose } from '../../lib/protocol';
import { IDENTITY } from '../../lib/scene/pose';
import { createPrimitiveGeometry } from '../../lib/geometry/primitives';
import type { SessionClient } from '../../lib/stream/client';

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

function disposeObject(object: Object3D) {
  object.traverse((child) => {
    if (!(child instanceof Mesh)) return;
    child.geometry.dispose();
    const materials = Array.isArray(child.material)
      ? child.material
      : [child.material];
    materials.forEach((material) => {
      for (const value of Object.values(material))
        if (value instanceof Texture) value.dispose();
      material.dispose();
    });
  });
}

function Asset({
  visual,
  selected,
  failed,
}: {
  visual: Exclude<Visual, { kind: 'primitive' }>;
  selected: boolean;
  failed: (message: string) => void;
}) {
  const [object, setObject] = useState<Group>();
  useEffect(() => {
    let active = true;
    let loaded: Group | undefined;
    new GLTFLoader().load(
      visual.url,
      (gltf) => {
        loaded = gltf.scene;
        if (!active) {
          disposeObject(loaded);
          return;
        }
        loaded.traverse((child) => {
          if (child instanceof Mesh && !child.geometry.getAttribute('normal'))
            child.geometry.computeVertexNormals();
        });
        setObject(loaded);
      },
      undefined,
      () => {
        if (active) failed(`Asset unavailable: ${visual.url}`);
      },
    );
    return () => {
      active = false;
      if (loaded) disposeObject(loaded);
    };
  }, [visual, failed]);
  useEffect(() => {
    object?.traverse((child) => {
      if (!(child instanceof Mesh)) return;
      const materials: Material[] = Array.isArray(child.material)
        ? child.material
        : [child.material];
      materials.forEach((material) => {
        if (material instanceof MeshStandardMaterial) {
          material.emissive.set(selected ? '#b86b16' : '#000000');
          material.emissiveIntensity = selected ? 0.4 : 0;
        }
      });
    });
  }, [object, selected]);
  return object ? (
    <group
      rotation={visual.assetUpAxis === 'Y' ? [Math.PI / 2, 0, 0] : [0, 0, 0]}
      scale={visual.scale ?? [1, 1, 1]}
    >
      <primitive object={object} dispose={null} />
    </group>
  ) : null;
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
  visual,
  selected,
  collider = false,
  fallbacks,
  warn,
}: {
  visual: Visual;
  selected: boolean;
  collider?: boolean;
  fallbacks: Visual[];
  warn: (message: string) => void;
}) {
  const [error, setError] = useState<string>();
  const failed = useMemo(
    () => (message: string) => {
      setError(message);
      warn(`${message} — using collision geometry or placeholder`);
    },
    [warn],
  );
  if (error) {
    const fallback = fallbacks.find((item) => item.kind === 'primitive');
    return fallback ? (
      <VisualObject
        visual={fallback}
        selected={selected}
        fallbacks={[]}
        warn={warn}
      />
    ) : (
      <Placeholder selected={selected} />
    );
  }
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
        <Asset visual={visual} selected={selected} failed={failed} />
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
  register: (id: number, object: Group | null) => void;
  warn: (message: string) => void;
}) {
  const ownSites = manifest.sites.filter(
    (site) => site.parentBodyKey === node.entityKey,
  );
  const visuals = node.visuals?.length ? node.visuals : (node.colliders ?? []);
  const isSelected = selected === node.entityKey;
  const pose: Pose = node.kind === 'static' ? node.initialPose : IDENTITY;
  useEffect(() => {
    if (!node.visuals.length)
      warn(
        `${node.entityKey}: no visual geometry — using ${node.colliders.length ? 'collision geometry' : 'placeholder'}`,
      );
  }, [node, warn]);
  return (
    <group
      ref={
        node.kind === 'body'
          ? (object) => register(node.streamId, object)
          : undefined
      }
      position={pose.p}
      quaternion={pose.q}
      visible={!hidden.has(node.entityKey)}
      onPointerDown={(event) => {
        event.stopPropagation();
        select(node.entityKey);
      }}
    >
      {visuals.length ? (
        visuals.map((visual, i) => (
          <VisualObject
            key={i}
            visual={visual}
            selected={isSelected}
            fallbacks={[...visuals, ...(node.colliders ?? [])]}
            warn={warn}
          />
        ))
      ) : (
        <Placeholder selected={isSelected} />
      )}
      {layers.colliders &&
        node.colliders?.map((visual, i) => (
          <VisualObject
            key={`collider-${i}`}
            visual={visual}
            selected={false}
            collider
            fallbacks={[]}
            warn={warn}
          />
        ))}
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
}: ViewportProps) {
  const selectedEntity =
    manifest.joints.find((joint) => joint.entityKey === selected)
      ?.anchorSiteKey ?? selected;
  const bodies = useRef(new Map<number, Group>());
  const elapsed = useRef({ time: 0, frames: 0 });
  const register = (id: number, object: Group | null) => {
    if (object) bodies.current.set(id, object);
    else bodies.current.delete(id);
  };
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
