import { useContext, useEffect, useMemo, useState } from 'react';
import { Edges } from '@react-three/drei';
import { useThree } from '@react-three/fiber';
import { Color, DoubleSide, Group, Mesh, MeshStandardMaterial, type Material } from 'three';
import type { SceneManifest, Visual } from '../../lib/protocol';
import { resolveVisuals, type LoadedVisual, type ResolvedVisuals, type VisualGroup } from '../../lib/scene/visual-assets';
import { createPrimitiveGeometry } from '../../lib/geometry/primitives';
import { IDENTITY } from '../../lib/scene/pose';
import { ViewerThemeContext } from '../../lib/scene/viewer-theme';

const skipPicking = () => {};

function Asset({
  visual,
  object,
  selected,
  collider,
  ghost = false,
}: {
  visual: Exclude<Visual, { kind: 'primitive' }>;
  object: Group;
  selected: boolean;
  collider: boolean;
  ghost?: boolean;
}) {
  const theme = useContext(ViewerThemeContext);
  useEffect(() => {
    object?.traverse((child) => {
      if (!(child instanceof Mesh)) return;
      child.castShadow = !ghost; child.receiveShadow = !ghost;
      if (ghost) child.raycast = skipPicking;
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
        material.transparent = ghost || collider || authored.transparent;
        material.opacity = ghost ? .24 : collider ? 0.7 : authored.opacity;
        if (ghost) material.depthWrite = false;
        if (material instanceof MeshStandardMaterial) {
          material.emissive.set(selected ? theme.accent : '#000000');
          if (!material.userData.authoredColor) material.userData.authoredColor = material.color.clone();
          material.color.copy(material.userData.authoredColor);
          if (!material.map) material.color.lerp(new Color(authored.transparent ? theme.glass : material.metalness > .5 ? theme.steel : theme.body), .35);
          if (ghost) { material.color.set(theme.accent); material.emissive.set(theme.accent); }
          material.emissiveIntensity = ghost ? .12 : selected ? 0.4 : 0;
        } else if (ghost && 'color' in material && material.color instanceof Color) material.color.set(theme.accent);
      });
    });
  }, [object, selected, collider, ghost, theme]);
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
  ghost = false,
}: {
  visual: Extract<Visual, { kind: 'primitive' }>;
  selected: boolean;
  collider: boolean;
  ghost?: boolean;
}) {
  const theme = useContext(ViewerThemeContext);
  const geometry = useMemo(
    () => createPrimitiveGeometry(visual.geometry),
    [visual.geometry],
  );
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <mesh geometry={geometry} castShadow={!ghost && visual.geometry.type !== 'plane'} receiveShadow={!ghost} {...(ghost ? { raycast: skipPicking } : {})}>
      {!ghost && !collider && visual.geometry.type === 'plane' ? <shadowMaterial opacity={theme.ao} depthWrite={false} /> : <meshStandardMaterial
        color={
          (ghost || collider)
            ? theme.accent
            : visual.geometry.type === 'plane'
                ? theme.floor
                : theme.body
        }
        roughness={0.7}
        metalness={0.12}
        side={DoubleSide}
        wireframe={collider}
        transparent={ghost || collider || visual.geometry.type === 'plane'}
        opacity={ghost ? .24 : collider ? 0.8 : visual.geometry.type === 'plane' ? theme.ao : 1}
        depthWrite={!ghost}
      />}
      {!ghost && !collider && visual.geometry.type !== 'plane' && <Edges threshold={35} color={selected ? theme.accent : theme.edge} opacity={selected ? 1 : theme.edgeOpacity} transparent raycast={skipPicking} />}
    </mesh>
  );
}

function VisualObject({
  loaded,
  selected,
  collider = false,
  ghost = false,
}: {
  loaded: LoadedVisual;
  selected: boolean;
  collider?: boolean;
  ghost?: boolean;
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
          ghost={ghost}
        />
      ) : (
        object && (
          <Asset
            visual={visual}
            object={object}
            selected={selected}
            collider={collider}
            ghost={ghost}
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

export type PhysicalNode = Exclude<SceneManifest['nodes'][number], { kind: 'group' }>;

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

export function NodeVisuals({
  node,
  selected,
  colliders,
  warn,
  ready,
  ghost = false,
}: {
  node: PhysicalNode;
  selected: boolean;
  colliders: boolean;
  warn: (message: string) => void;
  ready: (key: string) => void;
  ghost?: boolean;
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
          ghost ? null : <Placeholder selected={selected} />
        ) : (
          resolved?.visuals.map((loaded, i) => (
            <VisualObject key={i} loaded={loaded} selected={selected} ghost={ghost} />
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
