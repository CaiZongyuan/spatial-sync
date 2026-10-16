import { useState } from 'react';
import type { SceneManifest } from '../lib/protocol';

export function SceneTree({
  manifest,
  selected,
  select,
  hidden,
  toggle,
}: {
  manifest: SceneManifest;
  selected?: string;
  select: (key: string) => void;
  hidden: Set<string>;
  toggle: (key: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [collapsed, setCollapsed] = useState(new Set<string>());
  const row = (
    key: string,
    name: string,
    kind: string,
    depth: number,
    children?: React.ReactNode,
  ) => (
    <div key={key}>
      <div
        className={`tree-row ${selected === key ? 'selected' : ''}`}
        style={{ paddingLeft: 12 + depth * 14 }}
      >
        {children ? (
          <button
            className="tree-disclosure"
            aria-label={`${collapsed.has(key) ? 'Expand' : 'Collapse'} ${name}`}
            onClick={() =>
              setCollapsed((current) => {
                const next = new Set(current);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                return next;
              })
            }
          >
            {collapsed.has(key) ? '›' : '⌄'}
          </button>
        ) : (
          <span className="tree-spacer" />
        )}
        <button
          className="tree-select"
          onClick={() => select(key)}
          title={key}
          data-testid={`entity-${key}`}
        >
          <span className={`entity-icon ${kind}`}>
            {kind === 'group'
              ? '▱'
              : kind === 'site'
                ? '⊕'
                : kind === 'joint'
                  ? '◇'
                  : '▣'}
          </span>
          <span>{name}</span>
          <small>{kind}</small>
        </button>
        <button
          className="visibility-button"
          title={hidden.has(key) ? 'Show object' : 'Hide object'}
          aria-label={`${hidden.has(key) ? 'Show' : 'Hide'} ${name}`}
          onClick={() => toggle(key)}
        >
          {hidden.has(key) ? '○' : '●'}
        </button>
      </div>
      {!collapsed.has(key) && children}
    </div>
  );
  const renderNode = (
    node: SceneManifest['nodes'][number],
    depth: number,
  ): React.ReactNode => {
    const children = manifest.nodes.filter(
      (child) => child.parentEntityKey === node.entityKey,
    );
    const sites = manifest.sites.filter(
      (site) => site.parentBodyKey === node.entityKey,
    );
    const joints = manifest.joints.filter(
      (joint) => joint.childBodyKey === node.entityKey,
    );
    const items = [
      ...children.map((child) => renderNode(child, depth + 1)),
      ...sites.map((site) =>
        row(
          site.entityKey,
          site.entityKey.split('/').at(-1)!,
          'site',
          depth + 1,
        ),
      ),
      ...joints.map((joint) =>
        row(
          joint.entityKey,
          joint.entityKey.split('/').at(-1)!,
          'joint',
          depth + 1,
        ),
      ),
    ];
    return row(
      node.entityKey,
      node.name,
      node.kind,
      depth,
      items.length ? items : undefined,
    );
  };
  const filtered = query.trim()
    ? [
        ...manifest.nodes.map((node) => ({
          key: node.entityKey,
          name: node.name,
          kind: node.kind,
        })),
        ...manifest.sites.map((site) => ({
          key: site.entityKey,
          name: site.entityKey.split('/').at(-1)!,
          kind: 'site',
        })),
        ...manifest.joints.map((joint) => ({
          key: joint.entityKey,
          name: joint.entityKey.split('/').at(-1)!,
          kind: 'joint',
        })),
      ].filter((item) =>
        `${item.key} ${item.name}`.toLowerCase().includes(query.toLowerCase()),
      )
    : undefined;
  return (
    <aside className="tree-panel">
      <div className="panel-heading">
        <h2>Scene explorer</h2>
        <span>
          {manifest.nodes.length +
            manifest.sites.length +
            manifest.joints.length}
        </span>
      </div>
      <div className="tree-search">
        <span>⌕</span>
        <input
          aria-label="Filter objects"
          placeholder="Find an object…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="tree-body">
        {filtered ? (
          filtered.map((item) => row(item.key, item.name, item.kind, 0))
        ) : (
          <>
            {manifest.nodes
              .filter((node) => !node.parentEntityKey)
              .map((node) => renderNode(node, 0))}
            {manifest.sites
              .filter((site) => !site.parentBodyKey)
              .map((site) =>
                row(
                  site.entityKey,
                  site.entityKey.split('/').at(-1)!,
                  'site',
                  0,
                ),
              )}
          </>
        )}
      </div>
      <div className="tree-legend">
        <span>
          <i className="body-dot" /> Body
        </span>
        <span>
          <i className="site-dot" /> Site anchor
        </span>
      </div>
    </aside>
  );
}
