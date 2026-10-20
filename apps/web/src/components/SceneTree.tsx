import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties, type KeyboardEvent } from 'react';
import type { InfoStore } from '../lib/info/store';
import type { SceneManifest } from '../lib/protocol';
import { Icon, type IconName } from './Icon';

const kindLabels: Record<string, string> = { group: '组', body: '刚体', static: '静态', site: '站点', joint: '关节' };

export function SceneTree({ info, manifest, selected, select, hidden, toggle }: {
  info: InfoStore; manifest: SceneManifest; selected?: string; select: (key: string) => void;
  hidden: Set<string>; toggle: (key: string) => void;
}) {
  const [query, setQuery] = useState('');
  const tree = useRef<HTMLDivElement>(null);
  const { catalog } = useSyncExternalStore(info.subscribeMetadata, info.getMetadata);
  const phases = useSyncExternalStore(info.subscribeTree, info.getTree);
  const index = useMemo(() => {
    const children = new Map<string, SceneManifest['nodes']>();
    const sites = new Map<string, SceneManifest['sites']>();
    const joints = new Map<string, SceneManifest['joints']>();
    const parents = new Map<string, string>();
    const names = new Map<string, string>();
    for (const node of manifest.nodes) {
      const parent = node.parentEntityKey ?? '';
      children.set(parent, [...(children.get(parent) ?? []), node]);
      parents.set(node.entityKey, parent); names.set(node.entityKey, node.name);
    }
    for (const site of manifest.sites) {
      const parent = site.parentBodyKey ?? '';
      sites.set(parent, [...(sites.get(parent) ?? []), site]); parents.set(site.entityKey, parent);
    }
    for (const joint of manifest.joints) {
      const parent = joint.childBodyKey;
      joints.set(parent, [...(joints.get(parent) ?? []), joint]); parents.set(joint.entityKey, parent);
    }
    return { children, sites, joints, parents, names };
  }, [manifest]);
  const [collapsed, setCollapsed] = useState(() => new Set(manifest.nodes
    .filter(node => node.kind === 'body' && (index.sites.has(node.entityKey) || index.joints.has(node.entityKey)))
    .map(node => node.entityKey)));
  useEffect(() => {
    setCollapsed(new Set(manifest.nodes.filter(node => node.kind === 'body'
      && (index.sites.has(node.entityKey) || index.joints.has(node.entityKey))).map(node => node.entityKey)));
  }, [manifest, index]);
  useEffect(() => {
    if (catalog) setCollapsed(current => new Set([...current].filter(key => !catalog.entities[key])));
  }, [catalog]);
  const ancestors = useMemo(() => {
    const result = new Set<string>();
    let key = selected ? index.parents.get(selected) : undefined;
    while (key && !result.has(key)) { result.add(key); key = index.parents.get(key); }
    return result;
  }, [index, selected]);
  useEffect(() => {
    setCollapsed(current => new Set([...current].filter(key => !ancestors.has(key))));
  }, [ancestors]);
  useEffect(() => {
    const button = [...(tree.current?.querySelectorAll<HTMLButtonElement>('.tree-select') ?? [])]
      .find(button => button.dataset.entityKey === selected);
    button?.scrollIntoView({ block: 'nearest' });
  }, [collapsed, selected]);
  const changeExpanded = (key: string, expand?: boolean) => setCollapsed(current => {
    const next = new Set(current);
    if (expand ?? current.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const search = query.trim().toLowerCase();
  const highlight = (text: string) => {
    const start = text.toLowerCase().indexOf(search);
    return !search || start < 0 ? text : <>{text.slice(0, start)}<mark>{text.slice(start, start + search.length)}</mark>{text.slice(start + search.length)}</>;
  };
  const keyDown = (event: KeyboardEvent<HTMLButtonElement>, key: string, count: number) => {
    const buttons = [...(tree.current?.querySelectorAll<HTMLButtonElement>('.tree-select') ?? [])];
    const current = buttons.indexOf(event.currentTarget);
    let target: HTMLButtonElement | undefined;
    if (event.key === 'ArrowDown') target = buttons[current + 1];
    else if (event.key === 'ArrowUp') target = buttons[current - 1];
    else if (event.key === 'Home') target = buttons[0];
    else if (event.key === 'End') target = buttons.at(-1);
    else if (event.key === 'ArrowRight') {
      if (count && collapsed.has(key)) changeExpanded(key, true); else target = buttons[current + 1];
    } else if (event.key === 'ArrowLeft') {
      if (count && !collapsed.has(key)) changeExpanded(key, false);
      else target = buttons.find(button => button.dataset.entityKey === index.parents.get(key));
    } else return;
    event.preventDefault();
    if (target) { target.focus(); select(target.dataset.entityKey!); }
  };
  const row = (key: string, name: string, kind: string, depth: number, count = 0, children?: React.ReactNode) => {
    const device = Boolean(catalog?.entities[key]);
    const secondary = kind === 'site' || kind === 'joint';
    const icon = (device ? 'device' : kind) as IconName;
    const parent = index.parents.get(key);
    return <div key={key}>
      <div className={`tree-row ${secondary ? 'is-secondary' : ''} ${device ? 'is-device' : ''} ${hidden.has(key) ? 'is-hidden' : ''} ${ancestors.has(key) ? 'is-ancestor' : ''} ${selected === key ? 'selected' : ''}`}
        role="treeitem" aria-level={depth + 1} aria-selected={selected === key}
        aria-expanded={kind === 'group' && count ? !collapsed.has(key) : undefined}
        style={{ '--tree-depth': depth } as CSSProperties}>
        <span className="tree-indent" aria-hidden="true" />
        {count ? <button className="tree-disclosure" aria-label={`${collapsed.has(key) ? 'Expand' : 'Collapse'} ${name}`}
          aria-expanded={!collapsed.has(key)} onClick={() => changeExpanded(key)}><Icon name="chevron" className="tree-chevron" /></button> : <span className="tree-spacer" />}
        <button className="tree-select" onClick={() => select(key)} onKeyDown={event => keyDown(event, key, count)}
          title={key} data-testid={`entity-${key}`} data-entity-key={key}>
          <Icon name={icon} className={`entity-icon ${kind}`} /><span className="tree-name">{highlight(name)}
            {search && parent && <small className="tree-parent">{index.names.get(parent) ?? parent.split('/').at(-1)}</small>}</span>
          {device && phases[key] && <span className={`phase-badge phase-${phases[key]}`} data-testid={`phase-${key}`}>{phases[key]}</span>}
          {!search && count > 0 && collapsed.has(key) && <small className="tree-child-count">{count}</small>}
        </button>
        <button className="visibility-button" title={hidden.has(key) ? '显示对象' : '隐藏对象'}
          aria-label={`${hidden.has(key) ? 'Show' : 'Hide'} ${name}`} onClick={() => toggle(key)}>
          <Icon name={hidden.has(key) ? 'eyeOff' : 'eye'} className="tree-eye" /></button>
      </div>
      {children}
    </div>;
  };
  const secondaryRows = (key: string, depth: number) => [
    ...(index.sites.get(key) ?? []).map(site => row(site.entityKey, site.entityKey.split('/').at(-1)!, 'site', depth)),
    ...(index.joints.get(key) ?? []).map(joint => row(joint.entityKey, joint.entityKey.split('/').at(-1)!, 'joint', depth)),
  ];
  const renderNode = (node: SceneManifest['nodes'][number], depth: number): React.ReactNode => {
    const children = index.children.get(node.entityKey) ?? [];
    const secondaryCount = (index.sites.get(node.entityKey)?.length ?? 0) + (index.joints.get(node.entityKey)?.length ?? 0);
    const expanded = !collapsed.has(node.entityKey);
    // A body may own physical child bodies (centrifuge slots); only its annotations start folded.
    const content = <>{(expanded || node.kind === 'body') && children.map(child => renderNode(child, depth + 1))}
      {expanded && secondaryRows(node.entityKey, depth + 1)}</>;
    return row(node.entityKey, node.name, node.kind, depth,
      node.kind === 'body' ? secondaryCount : children.length + secondaryCount, content);
  };
  const filtered = search ? [
    ...manifest.nodes.map(node => ({ key: node.entityKey, name: node.name, kind: node.kind })),
    ...manifest.sites.map(site => ({ key: site.entityKey, name: site.entityKey.split('/').at(-1)!, kind: 'site' })),
    ...manifest.joints.map(joint => ({ key: joint.entityKey, name: joint.entityKey.split('/').at(-1)!, kind: 'joint' })),
  ].filter(item => `${item.key} ${item.name}`.toLowerCase().includes(search)) : undefined;
  return <aside className="tree-panel"><div className="panel-heading"><h2>场景树</h2>
    <span>{manifest.nodes.length + manifest.sites.length + manifest.joints.length} 个对象</span></div>
    <div className="tree-search"><Icon name="search" className="tree-search-icon" /><input aria-label="Filter objects"
      placeholder="筛选对象 · 名称或路径" value={query} onChange={event => setQuery(event.target.value)} /></div>
    <div className="tree-body" ref={tree} role="tree" aria-label="Scene objects">
      {filtered ? filtered.length ? filtered.map(item => row(item.key, item.name, item.kind, 0)) : <p className="tree-empty">没有匹配的对象。</p> : <>
        {manifest.nodes.filter(node => !node.parentEntityKey).map(node => renderNode(node, 0))}
        {secondaryRows('', 0)}</>}
    </div><div className="tree-legend">{(['group', 'body', 'static', 'site', 'joint'] as const).map(kind =>
      <span key={kind}><Icon name={kind} className="legend-icon" />{kindLabels[kind]}</span>)}</div>
  </aside>;
}
