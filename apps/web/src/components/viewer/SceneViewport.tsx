import { memo, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { SceneDiagnostics } from './SceneDiagnostics';
import {
  InstancedPrimitives,
  canInstance,
  type InstanceController,
} from './InstancedPrimitives';
import { Canvas, events, useFrame, useThree } from '@react-three/fiber';
import { Edges, GizmoHelper, GizmoViewport, Grid, OrbitControls } from '@react-three/drei';
import {
  DoubleSide,
  Box3,
  Color,
  Group,
  Mesh,
  MeshStandardMaterial,
  Vector3,
  type Material,
  type Object3D,
} from 'three';
import {
  resolveVisuals,
  type LoadedVisual,
  type ResolvedVisuals,
  type VisualGroup,
} from '../../lib/scene/visual-assets';
import type { SceneManifest, Visual, Pose } from '../../lib/protocol';
import { IDENTITY } from '../../lib/scene/pose';
import { createPrimitiveGeometry } from '../../lib/geometry/primitives';
import { ViewerThemeContext, useViewerTheme } from '../../lib/scene/viewer-theme';
import { CameraRig, type CameraPreset } from './CameraRig';
import { DeviceAnnotations, DeviceAnnotationLayer, type DeviceAnnotation } from './DeviceAnnotations';
import type { PlacedLabel } from '../../lib/scene/label-layout';
import type { SessionClient } from '../../lib/stream/client';
import type { FrameRecorder } from '../../lib/performance/frame-recorder';
import { PlatformRuntimeContext } from '../../lib/platform-runtime';

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
  source: string;
  visualCount: number;
  meshCount: number;
  normalsComplete: boolean;
  materials?: { type: string; opacity: number; depthWrite: boolean; castShadow: boolean; receiveShadow: boolean }[];
  min: number[];
  max: number[];
}
export type InspectVisuals = (key: string) => VisualGeometryInfo | undefined;

function isVisible(object: Object3D): boolean {
  for (let current: Object3D | null = object; current; current = current.parent)
    if (!current.visible) return false;
  return true;
}

const visibleEvents: typeof events = (state) => ({
  ...events(state),
  filter: (hits) => hits.filter((hit) => isVisible(hit.object)),
});

// Axes guide orientation; body meshes and Site marker spheres own picking.
const skipPicking = () => {};

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
  const theme = useContext(ViewerThemeContext);
  useEffect(() => {
    object?.traverse((child) => {
      if (!(child instanceof Mesh)) return;
      child.castShadow = true; child.receiveShadow = true;
      const materials: Material[] = Array.isArray(child.material)
        ? child.material
        : [child.material];
      materials.forEach((material) => {
        if (!material.userData.authoredAppearance) material.userData.authoredAppearance = {
          transparent: material.transparent, opacity: material.opacity,
          wireframe: 'wireframe' in material ? material.wireframe : false,
        };
        const authored = material.userData.authoredAppearance;
        if ('wireframe' in material) material.wireframe = collider || authored.wireframe;
        material.transparent = collider || authored.transparent;
        material.opacity = collider ? 0.7 : authored.opacity;
        if (material instanceof MeshStandardMaterial) {
          material.emissive.set(selected ? theme.accent : '#000000');
          if (!material.userData.authoredColor) material.userData.authoredColor = material.color.clone();
          material.color.copy(material.userData.authoredColor);
          if (!material.map) material.color.lerp(new Color(authored.transparent ? theme.glass : material.metalness > .5 ? theme.steel : theme.body), .35);
          material.emissiveIntensity = selected ? 0.4 : 0;
        }
      });
    });
  }, [object, selected, collider, theme]);
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
  const theme = useContext(ViewerThemeContext);
  const geometry = useMemo(
    () => createPrimitiveGeometry(visual.geometry),
    [visual.geometry],
  );
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <mesh geometry={geometry} castShadow={visual.geometry.type !== 'plane'} receiveShadow>
      {!collider && visual.geometry.type === 'plane' ? <shadowMaterial opacity={theme.ao} depthWrite={false} /> : <meshStandardMaterial
        color={
          collider
            ? theme.accent
            : visual.geometry.type === 'plane'
                ? theme.floor
                : theme.body
        }
        roughness={0.7}
        metalness={0.12}
        side={DoubleSide}
        wireframe={collider}
        transparent={collider || visual.geometry.type === 'plane'}
        opacity={collider ? 0.8 : visual.geometry.type === 'plane' ? theme.ao : 1}
      />}
      {!collider && visual.geometry.type !== 'plane' && <Edges threshold={35} color={selected ? theme.accent : theme.edge} opacity={selected ? 1 : theme.edgeOpacity} transparent raycast={skipPicking} />}
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

function useVisuals(groups: VisualGroup[], warn: (message: string) => void) {
  const [resolved, setResolved] = useState<ResolvedVisuals>();
  const invalidate = useThree((state) => state.invalidate);
  useEffect(() => { invalidate(); }, [resolved, invalidate]);
  useEffect(() => {
    const controller = new AbortController();
    let owned: ResolvedVisuals | undefined;
    setResolved(undefined);
    void resolveVisuals(groups, controller.signal, warn).then((result) => {
      owned = result;
      if (result) setResolved(result);
    });
    return () => {
      controller.abort();
      owned?.dispose();
    };
  }, [groups, warn]);
  return resolved;
}

function CollisionVisuals({
  visuals,
  warn,
}: {
  visuals: Visual[];
  warn: (message: string) => void;
}) {
  const groups = useMemo<VisualGroup[]>(
    () => [{ name: 'colliders', visuals }],
    [visuals],
  );
  const resolved = useVisuals(groups, warn);
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
  ready,
}: {
  node: PhysicalNode;
  selected: boolean;
  colliders: boolean;
  warn: (message: string) => void;
  ready: (key: string) => void;
}) {
  const groups = useMemo<VisualGroup[]>(
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
  const resolved = useVisuals(groups, report);
  useEffect(() => { if (resolved) ready(node.entityKey); }, [resolved, ready, node.entityKey]);
  return (
    <>
      <group
        name="visual-group"
        userData={{ source: resolved?.source ?? 'loading' }}
      >
        {resolved?.source === 'placeholder' ? (
          <Placeholder selected={selected} />
        ) : (
          resolved?.visuals.map((loaded, i) => (
            <VisualObject key={i} loaded={loaded} selected={selected} />
          ))
        )}
      </group>
      {colliders &&
        resolved &&
        resolved.source !== 'colliders' &&
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
  client,
  ready,
}: {
  node: PhysicalNode;
  manifest: SceneManifest;
  selected?: string;
  layers: DebugLayers;
  hidden: Set<string>;
  select: (key: string) => void;
  register: (key: string, id: number | undefined, object: Group | null) => void;
  warn: (message: string) => void;
  client: SessionClient;
  ready: (key: string) => void;
}) {
  const ownSites = manifest.sites.filter(
    (site) => site.parentBodyKey === node.entityKey,
  );
  const [hovered, setHovered] = useState(false);
  const isSelected = selected === node.entityKey;
  const pose: Pose = node.kind === 'static' ? node.initialPose : IDENTITY;
  return (
    <group
      userData={{ entityKey: node.entityKey, frameExcluded: node.visuals.length > 0 && node.visuals.every(visual => visual.kind === 'primitive' && visual.geometry.type === 'plane') }}
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
      onPointerOver={(event) => { event.stopPropagation(); setHovered(true); }}
      onPointerOut={() => setHovered(false)}
      onPointerDown={(event) => {
        if (
          !isVisible(event.eventObject) ||
          hidden.has(node.entityKey) ||
          !client.isEntityVisible(node.entityKey)
        )
          return;
        event.stopPropagation();
        select(node.entityKey);
      }}
    >
      <NodeVisuals
        node={node}
        selected={isSelected || hovered}
        colliders={layers.colliders}
        warn={warn}
        ready={ready}
      />
      {layers.origins && <axesHelper args={[0.18]} raycast={skipPicking} />}
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
          client={client}
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
  client,
}: {
  site: SceneManifest['sites'][number];
  visible: boolean;
  selected: boolean;
  select: (key: string) => void;
  client: SessionClient;
}) {
  const object = useRef<Group>(null);
  useFrame(() => {
    if (object.current)
      object.current.visible =
        visible &&
        (site.parentBodyKey != null || client.isEntityVisible(site.entityKey));
  });
  return (
    <group
      ref={object}
      userData={{ entityKey: site.entityKey }}
      position={site.localPose.p}
      quaternion={site.localPose.q}
      visible={visible}
      onPointerDown={(event) => {
        if (!isVisible(event.eventObject)) return;
        event.stopPropagation();
        select(site.entityKey);
      }}
    >
      <axesHelper args={[selected ? 0.26 : 0.14]} raycast={skipPicking} />
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
  renderContinuously = true,
  cameraPreset = 'perspective', annotations, highlightedKeys, onLabelLayout, capture, compact,
}: ViewportProps) {
  const invalidate = useThree((state) => state.invalidate);
  const gl = useThree((state) => state.gl);
  useEffect(() => {
    const autoReset = gl.info.autoReset;
    // GizmoHelper renders the world and its HUD separately; retain both passes.
    gl.info.autoReset = false;
    return () => { gl.info.autoReset = autoReset; };
  }, [gl]);
  const theme = useContext(ViewerThemeContext);
  useEffect(() => { invalidate(); }, [theme, invalidate]);
  useEffect(() => {
    if (renderContinuously) return;
    // Spatial samples mutate without changing the manifest. UI rate/heartbeat
    // updates leave the sample unchanged and do not need another 3D frame.
    let sample = client.buffer.latestSample;
    invalidate();
    return client.subscribe(() => {
      const next = client.buffer.latestSample;
      if (next?.epoch === sample?.epoch && next?.seq === sample?.seq
        && next?.sampleTimeMs === sample?.sampleTimeMs) return;
      sample = next;
      invalidate();
    });
  }, [client, renderContinuously, manifest, selected, layers, hidden, invalidate]);
  const selectedEntity =
    manifest.joints.find((joint) => joint.entityKey === selected)
      ?.anchorSiteKey ?? selected;
  const bodies = useRef(new Map<number, Group>());
  const physicalNodes = useRef(new Map<string, Group>());
  const instances = useRef<InstanceController | undefined>(undefined);
  const [boundsRevision, setBoundsRevision] = useState(0);
  const readyNodes = useRef(new Set<string>());
  const ready = useCallback((key?: string) => {
    if (key) readyNodes.current.add(key);
    setBoundsRevision(value => value + 1);
  }, []);
  const firstPose = useRef(false);
  const firstInstance = useRef(false);
  const captureFrames = useRef(0);
  const captured = useRef(false);
  const renderMode =
    new URLSearchParams(location.search).get('render') ?? 'auto';
  const useInstances =
    !capture &&
    !layers.colliders &&
    !layers.origins &&
    (renderMode === 'instanced' ||
      (renderMode === 'auto' && manifest.nodes.length >= 200));
  const instancedNodes = useMemo(
    () =>
      useInstances
        ? manifest.nodes.filter(
            (node): node is PhysicalNode =>
              node.kind !== 'group' && canInstance(node, manifest),
          )
        : [],
    [manifest, useInstances],
  );
  const instancedKeys = useMemo(
    () => new Set(instancedNodes.map((node) => node.entityKey)),
    [instancedNodes],
  );
  const registrationCounts = useCallback(
    () => ({
      registeredBodies:
        bodies.current.size + (instances.current?.registeredBodies ?? 0),
      registeredPhysicalNodes:
        physicalNodes.current.size +
        (instances.current?.registeredPhysicalNodes ?? 0),
      appliedBodies: rendered.current.size,
    }),
    [rendered],
  );
  const extraBounds = useCallback(() => instances.current?.bounds(), []);
  const inspectGeometry = useCallback((key: string) => inspectVisuals.current?.(key), [inspectVisuals]);
  const frameBounds = useCallback(() => {
    const box = new Box3();
    for (const object of physicalNodes.current.values()) {
      if (object.visible && !object.userData.frameExcluded) box.expandByObject(object);
    }
    const instanced = instances.current?.bounds();
    if (instanced) box.union(instanced);
    return box;
  }, []);
  const deviceBounds = useCallback((keys: string[]) => {
    const bounds = new Box3();
    for (const key of keys) {
      const object = physicalNodes.current.get(key);
      if (object?.visible) bounds.expandByObject(object);
      const instance = instances.current?.inspect(key);
      if (instance) bounds.union(new Box3(new Vector3(...instance.min), new Vector3(...instance.max)));
    }
    return bounds;
  }, []);
  const elapsed = useRef({ time: 0, frames: 0 });
  const register = useCallback((
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
  }, []);
  useEffect(() => {
    inspectVisuals.current = (key) => {
      const instance = instances.current?.inspect(key);
      if (instance) return instance;
      const group = physicalNodes.current
        .get(key)
        ?.children.find((child) => child.name === 'visual-group');
      if (
        !group ||
        group.userData.source === 'loading' ||
        group.children.length === 0
      )
        return;
      group.updateWorldMatrix(true, true);
      const bounds = new Box3().setFromObject(group);
      let meshCount = 0;
      let normalsComplete = true;
      const materials: NonNullable<VisualGeometryInfo['materials']> = [];
      group.traverse((object) => {
        if (object instanceof Mesh) {
          meshCount++;
          normalsComplete &&= Boolean(object.geometry.getAttribute('normal'));
          for (const material of Array.isArray(object.material) ? object.material : [object.material]) materials.push({
            type: material.type, opacity: material.opacity, depthWrite: material.depthWrite,
            castShadow: object.castShadow, receiveShadow: object.receiveShadow,
          });
        }
      });
      return {
        source: group.userData.source,
        visualCount: group.children.length,
        meshCount,
        normalsComplete,
        materials,
        min: bounds.min.toArray(),
        max: bounds.max.toArray(),
      };
    };
    return () => {
      inspectVisuals.current = undefined;
    };
  }, [inspectVisuals]);
  useFrame(({ gl, scene, camera }, delta) => {
    const start = performance.now();
    const data = client.samplePoses(start);
    for (const [key, object] of physicalNodes.current)
      object.visible = !hidden.has(key) && client.isEntityVisible(key);
    for (const node of manifest.nodes) {
      if (node.kind !== 'body') continue;
      const object = bodies.current.get(node.streamId);
      const index = client.buffer.indexForKey(node.entityKey);
      if (!object || index === undefined) continue;
      if (!client.buffer.hasPose(node.streamId)) {
        rendered.current.delete(node.entityKey);
        continue;
      }
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
    instances.current?.apply(data);
    if (!firstPose.current && client.buffer.latestSample) { firstPose.current = true; ready(); }
    if (!firstInstance.current && instances.current) { firstInstance.current = true; ready(); }
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
    // Samples above read the completed previous frame. Reset once before this
    // frame's world, shadows and optional HUD, rather than on each render pass.
    gl.info.reset();
    if (capture && !captured.current && readyNodes.current.size === manifest.nodes.filter(node => node.kind !== 'group').length
      && client.buffer.latestSample) {
      // The next frame lets CameraRig fit the fully loaded, posed geometry.
      if (++captureFrames.current < 3) invalidate();
      else {
        captured.current = true;
        gl.render(scene, camera);
        capture(gl.domElement.toDataURL('image/png'));
      }
    }
  });
  return (
    <>
      <SceneDiagnostics
        counts={registrationCounts}
        mode={useInstances ? 'instanced' : 'individual'}
        extraBounds={extraBounds}
        inspectVisuals={inspectGeometry}
      />
      <CameraRig preset={cameraPreset} bounds={frameBounds} revision={boundsRevision} />
      {/* The reference uses Three r128 legacy light units; current Three uses physical units. */}
      <hemisphereLight args={[theme.sky, theme.floor, theme.hemi * Math.PI]} />
      <directionalLight position={[3, 6, 3]} intensity={theme.key * Math.PI} color={theme.sun} castShadow
        shadow-mapSize={[1024, 1024]} shadow-camera-left={-5} shadow-camera-right={5}
        shadow-camera-top={5} shadow-camera-bottom={-5} shadow-bias={-.0005} shadow-radius={4} />
      {layers.grid && <Grid position={[0, -.004, 0]} args={[20, 20]} cellSize={.25}
        cellThickness={.5} cellColor={theme.grid} sectionSize={1} sectionThickness={.5}
        sectionColor={theme.grid} fadeDistance={6} fadeStrength={1.5} infiniteGrid raycast={skipPicking} />}
      <group name="physicsRoot" rotation={[-Math.PI / 2, 0, 0]}>
        {useInstances && (
          <InstancedPrimitives
            nodes={instancedNodes}
            client={client}
            selected={selectedEntity}
            highlightedKeys={highlightedKeys}
            hidden={hidden}
            rendered={rendered}
            controller={instances}
            select={select}
          />
        )}
        <axesHelper args={[0.6]} raycast={skipPicking} />
        {manifest.nodes
          .filter(
            (node): node is PhysicalNode =>
              node.kind !== 'group' && !instancedKeys.has(node.entityKey),
          )
          .map((node) => (
            <Body
              key={node.entityKey}
              {...{
                node,
                manifest,
                layers,
                hidden,
                select,
                register,
                warn,
                client,
                ready,
              }}
              selected={highlightedKeys?.has(node.entityKey) ? node.entityKey : selectedEntity}
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
              client={client}
            />
          ))}
      </group>
      {annotations && onLabelLayout && <DeviceAnnotations annotations={annotations} layout={onLabelLayout} boundsForKeys={deviceBounds} />}
      <OrbitControls
        makeDefault
        target={[0, 0.65, 0]}
        minDistance={0.3}
      />
      {!compact && !capture && <GizmoHelper alignment="bottom-left" margin={[40, 40]}>
        <group rotation={[-Math.PI / 2, 0, 0]}><GizmoViewport disabled hideNegativeAxes axisHeadScale={.8}
          axisColors={[theme.axisX, theme.axisY, theme.axisZ]} labelColor={theme.ground} /></group>
      </GizmoHelper>}
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
  renderContinuously?: boolean;
  cameraPreset?: CameraPreset;
  annotations?: DeviceAnnotation[];
  highlightedKeys?: Set<string>;
  compact?: boolean;
  onLabelLayout?: (labels: PlacedLabel[]) => void;
  capture?: (png: string) => void;
  captureKey?: string;
}

export const SceneViewport = memo(function SceneViewport(props: ViewportProps) {
  const reportSource = useContext(PlatformRuntimeContext), runtimeOwner = useId();
  const sourceVersion = props.client.readOnly ? undefined : props.manifest.source.newtonVersion ?? undefined;
  useEffect(() => {
    if (!sourceVersion) return;
    reportSource(runtimeOwner, sourceVersion);
    return () => reportSource(runtimeOwner);
  }, [reportSource, runtimeOwner, sourceVersion]);
  const running = useSyncExternalStore(props.client.subscribe, () => {
    const view = props.client.getSnapshot();
    return view.connection === 'connected' && view.sessionState === 'RUNNING';
  });
  const hasPose = useSyncExternalStore(props.client.subscribe, () => Boolean(props.client.buffer.latestSample));
  const theme = useViewerTheme();
  const [labels, setLabels] = useState<PlacedLabel[]>([]);
  const element = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(true);
  const receivedState = useRef(false);
  if (hasPose) receivedState.current = true;
  const displayScene = props.client.readOnly || receivedState.current;
  const continuous = props.renderContinuously !== false && (props.client.readOnly || running);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    if (element.current) observer.observe(element.current);
    return () => observer.disconnect();
  }, []);
  return (
    <div ref={element} className="viewport" data-testid="scene-viewport">
      {displayScene ? <Canvas
        flat
        shadows
        frameloop={!visible && !props.capture ? 'never' : continuous ? 'always' : 'demand'}
        events={visibleEvents}
        camera={{ position: [4.2, 3, 4.8], fov: 44, near: 0.01, far: 100 }}
        dpr={[1, 1.75]}
        gl={{ antialias: true, alpha: true, preserveDrawingBuffer: Boolean(props.capture) }}
        onPointerMissed={() => props.select('')}
      >
        <ViewerThemeContext.Provider value={theme}><SceneContents key={`${props.manifest.sceneRevision}:${props.captureKey ?? ''}`} {...props} renderContinuously={continuous} onLabelLayout={setLabels} /></ViewerThemeContext.Provider>
      </Canvas> : <div className="overview-loading" role="status">正在连接空间状态…</div>}
      {props.annotations && <DeviceAnnotationLayer labels={labels} annotations={props.annotations} selected={props.selected} select={props.select} />}
      {!props.compact && <><div className="viewport-label">
        <span className="live-dot" /> {props.client.readOnly ? '录制空间视图' : '实时空间视图'}{' '}
        <small>米 · Z 向上</small>
      </div>
      <div className="viewport-hint">
        拖动旋转 <span>·</span> 滚动缩放 <span>·</span> 点击查看
      </div></>}
    </div>
  );
});
