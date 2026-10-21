import { useContext, useEffect, useMemo, useRef, useState, type ComponentRef, type RefObject } from 'react';
import { Billboard, Line } from '@react-three/drei';
import { useFrame, useThree } from '@react-three/fiber';
import { Group, Mesh, Vector3 } from 'three';
import type { SceneManifest } from '../../lib/protocol';
import { previewBodies, type AvailablePreview } from '../../lib/plans/model';
import { ViewerThemeContext } from '../../lib/scene/viewer-theme';
import { NodeVisuals } from './NodeVisuals';

export interface PreviewInspection {
  policyDriven: boolean;
  points: number[][];
  target: number[];
  targetInView: boolean;
  projectedTarget: number[];
  dashOffset?: number;
  ghosts: Record<string, { p: number[]; q: number[]; meshes: number; opacity: number[]; source: string; visible: boolean }>;
}
export type InspectPreview = () => PreviewInspection | undefined;
const skipPicking = () => {};

export function useReducedMotion() {
  const [reduced, setReduced] = useState(() => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return reduced;
}

/** Visual overlay only: every body pose/identity is supplied by the actual Program preview. */
export function MotionPreviewLayer({ manifest, preview, hidden, warn, root, ready, inspect, reduced }: {
  manifest: SceneManifest; preview: AvailablePreview; hidden: Set<string>; warn: (message: string) => void;
  root: RefObject<Group | null>; ready: () => void; inspect?: RefObject<InspectPreview | undefined>; reduced: boolean;
}) {
  const theme = useContext(ViewerThemeContext);
  const { camera } = useThree();
  const target = useRef<Group>(null);
  const path = useRef<ComponentRef<typeof Line>>(null);
  const bodies = useMemo(() => previewBodies(manifest, preview), [manifest, preview]);
  useFrame(({ clock }) => {
    if (path.current && !reduced) path.current.material.dashOffset = -clock.elapsedTime * .15;
  });
  useEffect(() => {
    if (!inspect) return;
    const read: InspectPreview = () => {
      if (!root.current || !target.current) return;
      root.current.updateWorldMatrix(true, true);
      const projected = target.current.getWorldPosition(new Vector3()).project(camera);
      const line = path.current;
      const points: number[][] = [];
      if (line) {
        const starts = line.geometry.getAttribute('instanceStart'), ends = line.geometry.getAttribute('instanceEnd');
        if (starts && ends) {
          for (let index = 0; index < starts.count; index++) points.push([starts.getX(index), starts.getY(index), starts.getZ(index)]);
          if (ends.count) points.push([ends.getX(ends.count - 1), ends.getY(ends.count - 1), ends.getZ(ends.count - 1)]);
        }
      }
      const ghosts: PreviewInspection['ghosts'] = {};
      for (const body of root.current.children) {
        const key = body.userData.ghostEntityKey;
        if (typeof key !== 'string') continue;
        const opacity: number[] = []; let meshes = 0;
        body.traverse(child => {
          if (!(child instanceof Mesh)) return;
          meshes++;
          for (const material of Array.isArray(child.material) ? child.material : [child.material]) opacity.push(material.opacity);
        });
        ghosts[key] = { p: body.position.toArray(), q: body.quaternion.toArray(), meshes, opacity,
          source: String(body.getObjectByName('visual-group')?.userData.source ?? 'loading'), visible: body.visible };
      }
      return { policyDriven: preview.policyDriven, points, ghosts, target: target.current.position.toArray(),
        projectedTarget: [projected.x * .5 + .5, .5 - projected.y * .5, projected.z],
        targetInView: Math.abs(projected.x) <= 1 && Math.abs(projected.y) <= 1 && Math.abs(projected.z) <= 1,
        dashOffset: path.current?.material.dashOffset };
    };
    inspect.current = read;
    return () => { if (inspect.current === read) inspect.current = undefined; };
  }, [inspect, root, preview.policyDriven, camera]);
  return <group name="motion-preview" ref={root}>
    {!preview.policyDriven && preview.points.length > 1 && <Line ref={path} name="preview-tcp-path" points={preview.points}
      color={theme.accent} lineWidth={1.6} dashed dashSize={.055} gapSize={.035} transparent opacity={.9} raycast={skipPicking} />}
    <group ref={target} name="preview-target" position={preview.targetPose.p}><Billboard><mesh raycast={skipPicking}>
      <ringGeometry args={[.045, .055, 48]} /><meshBasicMaterial color={theme.accent} transparent opacity={.95} depthWrite={false} />
    </mesh><mesh raycast={skipPicking}><circleGeometry args={[.012, 24]} /><meshBasicMaterial color={theme.accent} depthWrite={false} /></mesh></Billboard></group>
    {bodies.map(({ node, pose }) => <group key={node.entityKey} name={`ghost-body:${node.entityKey}`} userData={{ ghostEntityKey: node.entityKey }}
      position={pose.p} quaternion={pose.q} visible={!hidden.has(node.entityKey)}>
      <NodeVisuals node={node} selected={false} colliders={false} ghost warn={warn} ready={ready} />
    </group>)}
  </group>;
}
