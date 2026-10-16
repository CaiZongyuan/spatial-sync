import { Group, Mesh, Texture, type Object3D } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { Visual } from '../protocol';

export interface VisualTier {
  name: 'visuals' | 'fallbackVisuals' | 'colliders';
  visuals: Visual[];
}
export interface LoadedVisual {
  visual: Visual;
  object: Group | undefined;
}
export interface ResolvedVisuals {
  tier: VisualTier['name'] | 'placeholder';
  visuals: LoadedVisual[];
  dispose: () => void;
}

export function disposeObject(object: Object3D) {
  object.traverse((child) => {
    if (!(child instanceof Mesh)) return;
    child.geometry.dispose();
    for (const material of Array.isArray(child.material)
      ? child.material
      : [child.material]) {
      for (const value of Object.values(material))
        if (value instanceof Texture) value.dispose();
      material.dispose();
    }
  });
}

export async function loadVisualAsset(
  visual: Visual,
): Promise<Group | undefined> {
  if (visual.kind === 'primitive') return;
  let object: Group | undefined;
  try {
    object = (await new GLTFLoader().loadAsync(visual.url)).scene;
    object.traverse((child) => {
      if (child instanceof Mesh && !child.geometry.getAttribute('normal'))
        child.geometry.computeVertexNormals();
    });
    return object;
  } catch (error) {
    if (object) disposeObject(object);
    throw new Error(
      `${visual.url}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

/** A tier is an entire simultaneous visual group. Never mix partially loaded tiers. */
export async function resolveVisualTiers(
  tiers: VisualTier[],
  signal: AbortSignal,
  warn: (message: string) => void,
  load = loadVisualAsset,
): Promise<ResolvedVisuals | undefined> {
  let reportedFailure = false;
  for (const [index, tier] of tiers.entries()) {
    if (signal.aborted) return;
    if (!tier.visuals.length) continue;
    const loaded = await Promise.allSettled(
      tier.visuals.map(async (visual) => ({
        visual,
        object: await load(visual),
      })),
    );
    const completed = loaded
      .filter(
        (item): item is PromiseFulfilledResult<LoadedVisual> =>
          item.status === 'fulfilled',
      )
      .map((item) => item.value);
    const dispose = () =>
      completed.forEach((item) => {
        if (item.object) disposeObject(item.object);
      });
    if (signal.aborted) {
      dispose();
      return;
    }
    const failures = loaded.filter((item) => item.status === 'rejected');
    if (failures.length) {
      dispose();
      reportedFailure = true;
      warn(
        `${tier.name}: Asset unavailable: ${failures.map((item) => (item.reason instanceof Error ? item.reason.message : String(item.reason))).join('; ')}; trying the next visual group`,
      );
      continue;
    }
    if (index > 0 && !reportedFailure)
      warn(`Using ${tier.name} fallback group (${completed.length} visuals)`);
    return { tier: tier.name, visuals: completed, dispose };
  }
  if (signal.aborted) return;
  warn('No loadable visual group — using placeholder geometry');
  return { tier: 'placeholder', visuals: [], dispose: () => {} };
}
