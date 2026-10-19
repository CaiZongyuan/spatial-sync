export interface LabelAnchor { key: string; x: number; y: number; width: number; height: number }
export interface PlacedLabel extends LabelAnchor { anchorX: number; anchorY: number }

/** Place labels in viewport rows, keeping every admitted label inside the frame. */
export function placeLabels(anchors: LabelAnchor[], width: number, height: number): PlacedLabel[] {
  const padding = 12, gap = 8;
  const placed: PlacedLabel[] = [];
  const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(value, high));
  const sorted = anchors.slice().sort((a, b) => a.y - b.y || a.x - b.x || a.key.localeCompare(b.key));
  for (const anchor of sorted) {
    const w = Math.min(anchor.width, width - padding * 2);
    if (w <= 0 || anchor.height > height - padding * 2) continue;
    const preferredX = clamp(anchor.x - w / 2, padding, width - padding - w);
    const preferredY = clamp(anchor.y - anchor.height - 14, padding, height - padding - anchor.height);
    const candidates = [preferredY];
    for (let y = padding; y <= height - padding - anchor.height; y += anchor.height + gap) candidates.push(y);
    let best: PlacedLabel | undefined;
    let distance = Infinity;
    for (const y of candidates) {
      for (const x of [preferredX, padding, width - padding - w]) {
        if (placed.some(label => x < label.x + label.width + gap && x + w + gap > label.x && y < label.y + label.height + gap && y + anchor.height + gap > label.y)) continue;
        const cost = (x + w / 2 - anchor.x) ** 2 + (y - preferredY) ** 2;
        if (cost < distance) {
          distance = cost;
          best = { ...anchor, width: w, x, y, anchorX: anchor.x, anchorY: anchor.y };
        }
      }
    }
    if (best) placed.push(best);
  }
  return placed;
}
