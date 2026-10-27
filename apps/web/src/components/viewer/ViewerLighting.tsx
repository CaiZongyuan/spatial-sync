import { useEffect } from 'react';
import { useThree } from '@react-three/fiber';
import { PMREMGenerator } from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import type { ViewerTheme } from '../../lib/scene/viewer-theme';
import type { SceneManifest } from '../../lib/protocol';

export function hasAuthoredVisuals(manifest: SceneManifest) {
  return manifest.nodes.some(node => 'visuals' in node && node.visuals.some(visual => visual.kind === 'glb'));
}

/** Local image-based lighting: generated once per mounted renderer, with no remote assets. */
export function ViewerLighting({ theme, radius = 5, environment = true }: {
  theme: ViewerTheme; radius?: number; environment?: boolean;
}) {
  const { gl, scene, invalidate } = useThree();
  useEffect(() => {
    if (!environment) return;
    const generator = new PMREMGenerator(gl);
    const room = new RoomEnvironment();
    const reflection = generator.fromScene(room, .04, .1, 100, { size: 128 });
    room.dispose();
    generator.dispose();
    const previous = scene.environment;
    const previousIntensity = scene.environmentIntensity;
    scene.environment = reflection.texture;
    scene.environmentIntensity = .6;
    invalidate();
    return () => {
      if (scene.environment === reflection.texture) {
        scene.environment = previous;
        scene.environmentIntensity = previousIntensity;
      }
      reflection.dispose();
    };
  }, [gl, scene, invalidate, environment]);
  return <>
    <hemisphereLight args={[theme.sky, theme.floor, theme.hemi * Math.PI * (environment ? .25 : 1)]} />
    <directionalLight position={[3, 6, 3]} intensity={theme.key * Math.PI} color={theme.sun} castShadow
      shadow-mapSize={[1024, 1024]} shadow-camera-left={-radius} shadow-camera-right={radius}
      shadow-camera-top={radius} shadow-camera-bottom={-radius} shadow-bias={-.0005} shadow-radius={4} />
  </>;
}
