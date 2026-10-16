import { BoxGeometry, Group, Mesh, MeshStandardMaterial } from 'three';
import { expect, it, vi } from 'vitest';
import type { Visual } from '../protocol';
import { IDENTITY } from './pose';
import { resolveVisualTiers } from './visual-tiers';

const external: Visual = {
  kind: 'glb',
  url: '/binding.glb',
  assetUpAxis: 'Y',
  scale: [2, 3, 4],
  localPose: { p: [1, 2, 3], q: [0, 0, 0, 1] },
};
const native: Visual = {
  kind: 'mesh',
  meshId: 'hash',
  url: '/mesh.glb',
  assetUpAxis: 'Z',
  scale: [2, 3, 4],
  localPose: IDENTITY,
};
const primitive: Visual = {
  kind: 'primitive',
  geometry: { type: 'box', halfExtents: [0.1, 0.2, 0.3] },
  localPose: IDENTITY,
};
const object = () =>
  new Group().add(
    new Mesh(new BoxGeometry(1, 2, 3), new MeshStandardMaterial()),
  );

it('uses the entire simultaneous native group when the external binding fails, including mesh and primitive', async () => {
  const mesh = object();
  const load = vi.fn(async (visual: Visual) => {
    if (visual === external) throw new Error('binding.glb 404');
    return visual.kind === 'primitive' ? undefined : mesh;
  });
  const warn = vi.fn();
  const resolved = await resolveVisualTiers(
    [
      { name: 'visuals', visuals: [external] },
      { name: 'fallbackVisuals', visuals: [native, primitive] },
      { name: 'colliders', visuals: [primitive] },
    ],
    new AbortController().signal,
    warn,
    load,
  );
  expect(resolved!.tier).toBe('fallbackVisuals');
  expect(resolved!.visuals.map((item) => item.visual)).toEqual([
    native,
    primitive,
  ]);
  expect(resolved!.visuals[0].object).toBe(mesh);
  expect(load).toHaveBeenCalledTimes(3);
  expect(warn).toHaveBeenCalledTimes(1);
  resolved!.dispose();
});

it('disposes partial asset success before switching the entire failed tier', async () => {
  const partiallyLoaded = object();
  const mesh = partiallyLoaded.children[0] as Mesh;
  const disposeGeometry = vi.spyOn(mesh.geometry, 'dispose');
  const disposeMaterial = vi.spyOn(
    mesh.material as MeshStandardMaterial,
    'dispose',
  );
  const resolved = await resolveVisualTiers(
    [
      { name: 'visuals', visuals: [native, external] },
      { name: 'colliders', visuals: [primitive] },
    ],
    new AbortController().signal,
    vi.fn(),
    async (visual) => {
      if (visual === external) throw new Error('missing');
      return visual === native ? partiallyLoaded : undefined;
    },
  );
  expect(disposeGeometry).toHaveBeenCalledTimes(1);
  expect(disposeMaterial).toHaveBeenCalledTimes(1);
  expect(resolved!.tier).toBe('colliders');
  expect(resolved!.visuals.map((item) => item.visual)).toEqual([primitive]);
});

it('keeps the primary full group exclusively when all its assets load', async () => {
  const load = vi.fn(async (visual: Visual) =>
    visual.kind === 'primitive' ? undefined : object(),
  );
  const resolved = await resolveVisualTiers(
    [
      { name: 'visuals', visuals: [external, primitive] },
      { name: 'fallbackVisuals', visuals: [native] },
      { name: 'colliders', visuals: [primitive] },
    ],
    new AbortController().signal,
    vi.fn(),
    load,
  );
  expect(resolved!.tier).toBe('visuals');
  expect(resolved!.visuals.map((item) => item.visual)).toEqual([
    external,
    primitive,
  ]);
  expect(resolved!.visuals[0].visual).toBe(external);
  expect(load).toHaveBeenCalledTimes(2);
  resolved!.dispose();
});

it('supports mesh collider fallback and reaches a placeholder only after every tier fails', async () => {
  const mesh = object();
  const fallback = await resolveVisualTiers(
    [
      { name: 'visuals', visuals: [external] },
      { name: 'colliders', visuals: [native] },
    ],
    new AbortController().signal,
    vi.fn(),
    async (visual) => {
      if (visual === external) throw new Error('missing');
      return mesh;
    },
  );
  expect(fallback!.tier).toBe('colliders');
  expect(fallback!.visuals[0].object).toBe(mesh);
  fallback!.dispose();
  const placeholder = await resolveVisualTiers(
    [
      { name: 'visuals', visuals: [external] },
      { name: 'fallbackVisuals', visuals: [native] },
    ],
    new AbortController().signal,
    vi.fn(),
    async () => {
      throw new Error('missing');
    },
  );
  expect(placeholder!.tier).toBe('placeholder');
  expect(placeholder!.visuals).toEqual([]);
});

it('releases assets that finish after unmount and never loads or reports the next tier', async () => {
  const lateObject = object();
  const dispose = vi.spyOn(
    (lateObject.children[0] as Mesh).geometry,
    'dispose',
  );
  let finish!: (value: Group) => void;
  const delayed = new Promise<Group>((resolve) => {
    finish = resolve;
  });
  const controller = new AbortController();
  const warn = vi.fn();
  const load = vi.fn(() => delayed);
  const pending = resolveVisualTiers(
    [
      { name: 'visuals', visuals: [external] },
      { name: 'fallbackVisuals', visuals: [native] },
    ],
    controller.signal,
    warn,
    load,
  );
  controller.abort();
  finish(lateObject);
  expect(await pending).toBeUndefined();
  expect(dispose).toHaveBeenCalledTimes(1);
  expect(load).toHaveBeenCalledTimes(1);
  expect(warn).not.toHaveBeenCalled();
});
