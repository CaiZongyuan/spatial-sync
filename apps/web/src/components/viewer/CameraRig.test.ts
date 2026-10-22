import { beforeEach, expect, test, vi } from 'vitest';
import { Box3, Vector3 } from 'three';

const harness = vi.hoisted(() => ({
  frame: undefined as undefined | ((state: unknown) => void),
  state: { camera: undefined as unknown, size: { width: 1000, height: 700 },
    set: vi.fn(), invalidate: vi.fn(), controls: { target: undefined as unknown, update: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn() } },
}));
vi.mock('react', () => ({
  useMemo: (create: () => unknown) => create(),
  useRef: (current: unknown) => ({ current }),
  useEffect: (effect: () => void) => effect(),
}));
vi.mock('@react-three/fiber', () => ({
  useThree: () => harness.state,
  useFrame: (frame: (state: unknown) => void) => { harness.frame = frame; },
}));
import { CameraRig } from './CameraRig';

beforeEach(() => {
  harness.state.controls.target = new Vector3();
  harness.state.set.mockImplementation(({ camera }) => { harness.state.camera = camera; });
});

test('a distant body cannot turn initial framing into a million-metre camera distance', () => {
  const bounds = new Box3(new Vector3(-1, 0, -1), new Vector3(1e9, 2, 1));
  CameraRig({ preset: 'perspective', bounds: () => bounds, revision: 1, maxRadius: 20, maxDistance: 100 });
  harness.frame!(harness.state);
  const camera = harness.state.camera as { position: Vector3 };
  expect(camera.position.distanceTo(harness.state.controls.target as Vector3)).toBeLessThanOrEqual(100);
});
