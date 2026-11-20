import { expect, it } from 'vitest';
import { motionVersionPath, referenceMatches } from './motion';

it('requires the detail and ghost to agree with the selected immutable reference and robot', () => {
  const sha256 = 'a'.repeat(64);
  const pin = { id: `reference-motion/${sha256}@1`, sha256, asset: 'robot/probe@2', assetContentHash: 'b'.repeat(64) };
  const detail = { ...pin, motion: pin, source: {}, report: {} };
  const preview = { ...pin, loop: false, ghost: { links: [], samples: [] } };

  expect(motionVersionPath(pin.id, 'reference-motion')).toBe(`/library/reference-motions/${sha256}/1`);
  expect(() => motionVersionPath(`motion-clip/${sha256}@1`, 'reference-motion')).toThrow();
  expect(referenceMatches(pin, detail, preview)).toBe(true);
  expect(referenceMatches(pin, detail, { ...preview, asset: 'robot/probe@1' })).toBe(false);
  expect(referenceMatches(pin, { ...detail, sha256: 'c'.repeat(64) }, preview)).toBe(false);
});
