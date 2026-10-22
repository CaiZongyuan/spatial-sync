import { expect, test } from 'vitest';
import { Box3, OrthographicCamera, PerspectiveCamera, Vector3 } from 'three';
import { defaultFraming, fitCamera, FramingState, sceneFramingBounds, updateProjection } from './camera-framing';

const box = (x: number, y = 0, z = 0) => new Box3(new Vector3(x - .3, y, z - .3), new Vector3(x + .3, y + 1, z + .3));
const corners = (bounds: Box3) => [bounds.min.x, bounds.max.x].flatMap(x =>
  [bounds.min.y, bounds.max.y].flatMap(y => [bounds.min.z, bounds.max.z].map(z => new Vector3(x, y, z))));
const ready = () => true;
const loading = () => false;

for (const projection of ['perspective', 'isometric']) test(`${projection} fits the healthy cluster, preserving translated scenes`, () => {
  const healthy = [box(-1), box(0), box(1)];
  const outliers = [box(1e9), new Box3(new Vector3(-1e6, -1e6, -1e6), new Vector3(1e6, 1e6, 1e6))];
  const bounds = sceneFramingBounds([...healthy, ...outliers]);
  const expected = healthy.reduce((all, item) => all.union(item), new Box3());
  expect(bounds).toEqual(expected);
  const camera = projection === 'perspective' ? new PerspectiveCamera(44) : new OrthographicCamera();
  const result = fitCamera(camera, bounds, 1.5, 20, 100);
  expect(result.center).toEqual(expected.getCenter(new Vector3()));
  expect(camera.position.distanceTo(result.center)).toBeLessThanOrEqual(100);
  camera.updateMatrixWorld(true);
  for (const point of corners(expected)) {
    const projected = point.project(camera);
    expect(Math.abs(projected.x)).toBeLessThanOrEqual(1);
    expect(Math.abs(projected.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(projected.z)).toBeLessThanOrEqual(1);
  }
  const shift = new Vector3(1e6, -2e6, 3e6);
  const moved = sceneFramingBounds([...healthy, ...outliers].map(item => item.clone().translate(shift)));
  expect(moved.min).toEqual(bounds.min.clone().add(shift));
  expect(moved.max).toEqual(bounds.max.clone().add(shift));
});

test('empty, nonfinite and huge individual bounds use the previous fit or lab default', () => {
  const previous = box(5);
  const broken = new Box3(new Vector3(-Infinity, 0, 0), new Vector3(Infinity, 1, 1));
  expect(sceneFramingBounds([broken, new Box3()], previous)).toEqual(previous);
  expect(sceneFramingBounds([broken])).toEqual(defaultFraming());
  expect(sceneFramingBounds([box(0)])).toEqual(box(0));
});

test('equal remote clusters have a deterministic view policy, not a physical classification', () => {
  const left = box(-100), right = box(100);
  expect(sceneFramingBounds([left, right])).toEqual(sceneFramingBounds([right, left]));
  expect(sceneFramingBounds([left, right], right)).toEqual(right);
});

for (const projection of ['perspective', 'isometric']) test(`${projection} preserves a large far-translated static asset's full bounds`, () => {
  const asset = new Box3(new Vector3(1e6, -100, -100), new Vector3(1e6 + 500, 100, 100));
  const camera = projection === 'perspective' ? new PerspectiveCamera(44) : new OrthographicCamera();
  const fitted = fitCamera(camera, asset, .5);
  expect(fitted.center).toEqual(asset.getCenter(new Vector3()));
  expect(camera.position.distanceTo(fitted.center)).toBeGreaterThan(100);
  camera.updateMatrixWorld(true);
  for (const point of corners(asset)) {
    const projected = point.project(camera);
    expect(Math.abs(projected.x)).toBeLessThanOrEqual(1);
    expect(Math.abs(projected.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(projected.z)).toBeLessThanOrEqual(1);
  }
});

test('one persistent viewport waits for settled readiness and stops implicit fits after user takeover', () => {
  const viewer = new FramingState();
  expect(viewer.takeFit(loading)).toBe(false); // First pose while GLB/initial preview is still loading.
  expect(viewer.takeFit(loading)).toBe(false); // Readiness can arrive after any delay.
  viewer.claimView(); // Orbit, pan or wheel before readiness owns the view.
  expect(viewer.takeFit(ready)).toBe(false);
  viewer.requestFit();
  expect(viewer.takeFit(loading)).toBe(true); // Explicit fit is available during loading.
  expect(viewer.takeFit(ready)).toBe(false); // Late revision cannot reset the view.
  const other = new FramingState();
  expect(other.takeFit(loading)).toBe(false);
  expect(other.takeFit(ready)).toBe(true); // New scene/independent viewport owns its own initialization.
  expect(other.takeFit(ready)).toBe(false);
});

test('resize changes projection without moving the user camera, target or zoom', () => {
  const camera = new OrthographicCamera();
  const fitted = fitCamera(camera, box(5), 1.5);
  camera.zoom = 3;
  const position = camera.position.clone(), orientation = camera.quaternion.clone();
  updateProjection(camera, .6, fitted.half);
  expect(camera.position).toEqual(position);
  expect(camera.quaternion.toArray()).toEqual(orientation.toArray());
  expect(camera.zoom).toBe(3);
  expect(camera.left).toBe(-fitted.half);
});
