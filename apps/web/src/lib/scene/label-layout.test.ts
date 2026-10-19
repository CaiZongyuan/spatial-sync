import { expect, it } from 'vitest';
import { placeLabels } from './label-layout';

it('separates crowded device labels and keeps edge anchors inside the viewport', () => {
  const anchors = Array.from({ length: 8 }, (_, i) => ({ key: String(i), x: i < 4 ? 0 : 400, y: i < 4 ? 0 : 300, width: 132, height: 28 }));
  const labels = placeLabels(anchors, 400, 300);
  expect(labels).toHaveLength(8);
  for (const label of labels) {
    expect(label.x).toBeGreaterThanOrEqual(12);
    expect(label.x + label.width).toBeLessThanOrEqual(388);
    expect(label.y).toBeGreaterThanOrEqual(12);
    expect(label.y + label.height).toBeLessThanOrEqual(288);
    for (const other of labels.filter(other => other !== label))
      expect(label.x >= other.x + other.width || label.x + label.width <= other.x || label.y >= other.y + other.height || label.y + label.height <= other.y).toBe(true);
  }
  expect(placeLabels(anchors.slice().reverse(), 400, 300)).toEqual(labels);
});
