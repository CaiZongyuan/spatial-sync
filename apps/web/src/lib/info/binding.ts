import type { Catalog, SceneManifest } from '../protocol';

/** Logical ownership follows exported links, even when names use unrelated paths. */
export function deviceKey(
  manifest: SceneManifest,
  catalog: Catalog | undefined,
  selected: string | undefined,
): string | undefined {
  const visited = new Set<string>();
  while (selected && !visited.has(selected)) {
    if (catalog?.entities[selected]) return selected;
    visited.add(selected);
    const node = manifest.nodes.find((node) => node.entityKey === selected);
    const site = manifest.sites.find((site) => site.entityKey === selected);
    const joint = manifest.joints.find((joint) => joint.entityKey === selected);
    selected =
      node?.parentEntityKey ??
      site?.parentBodyKey ??
      joint?.childBodyKey ??
      undefined;
  }
}
