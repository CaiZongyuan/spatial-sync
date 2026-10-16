import { expect, it } from 'vitest';
import type { Catalog, SceneManifest } from '../protocol';
import { deviceKey } from './binding';

it('resolves device ownership through Manifest links for Bodies, Sites and Joints', () => {
  const manifest = {
    nodes: [
      { kind: 'group', entityKey: '/device' },
      {
        kind: 'body',
        entityKey: '/unrelated/path/body',
        parentEntityKey: '/device',
      },
    ],
    sites: [
      { entityKey: '/marker/tcp', parentBodyKey: '/unrelated/path/body' },
    ],
    joints: [{ entityKey: '/joint', childBodyKey: '/unrelated/path/body' }],
  } as SceneManifest;
  const catalog: Catalog = {
    sceneRevision: 'scene',
    types: {},
    entities: Object.fromEntries(
      ['/device', '/unrelated'].map((key) => [
        key,
        {
          key,
          type: 'device/v1',
          program: 'virtual',
          config: {},
          interlocks: {},
          unplaced: false,
        },
      ]),
    ),
  };
  expect(deviceKey(manifest, catalog, '/marker/tcp')).toBe('/device');
  expect(deviceKey(manifest, catalog, '/joint')).toBe('/device');
  expect(deviceKey(manifest, catalog, '/unrelated/path/body')).toBe('/device');
  expect(deviceKey(manifest, catalog, '/unknown')).toBeUndefined();
});
