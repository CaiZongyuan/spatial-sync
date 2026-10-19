import { createFileRoute, Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { getSceneLibrary, sourceLabel, rootLabel, type SceneSource, type SceneVersion } from '../lib/library';
import { SceneThumbnails } from '../components/SceneThumbnails';
import { LaunchStatus } from '../components/SceneStatus';

type Search = { q?: string; source?: SceneSource; launchable?: boolean; view?: 'card' | 'list' };
const viewKey = 'lab.scenes.view';
export const Route = createFileRoute('/scenes/')({
  validateSearch: (value: Record<string, unknown>): Search => ({
    q: typeof value.q === 'string' && value.q ? value.q : undefined,
    source: value.source === 'synthetic' || value.source === 'newton' ? value.source : undefined,
    launchable: value.launchable === true || value.launchable === 'true' ? true
      : value.launchable === false || value.launchable === 'false' ? false : undefined,
    view: value.view === 'card' || value.view === 'list' ? value.view : undefined,
  }), component: SceneLibrary,
});

function SceneLibrary() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const [preferred, setPreferred] = useState<'card' | 'list'>(() => {
    try { return localStorage.getItem(viewKey) === 'list' ? 'list' : 'card'; } catch { return 'card'; }
  });
  const view = search.view ?? preferred;
  const [scenes, setScenes] = useState<SceneVersion[]>();
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(undefined);
    void getSceneLibrary(search, controller.signal).then(value => setScenes(value.scenes))
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [search.q, search.source, search.launchable, attempt]);
  const change = (next: Partial<Search>) => void navigate({ search: previous => ({ ...previous, ...next }), replace: true });
  return <main className="scene-library-page">
    <header className="scene-page-heading"><div><h1>场景库</h1><p>选择一个不可变版本，检查场景后启动会话。</p></div>
      <span className="scene-result-count">{scenes ? `${scenes.length} 个版本` : '读取中…'}</span></header>
    <section className="scene-toolbar" aria-label="场景筛选">
      <label className="scene-search"><span aria-hidden="true">⌕</span><input aria-label="搜索场景" type="search" placeholder="搜索场景…" value={search.q ?? ''} onChange={event => change({ q: event.target.value || undefined })} /></label>
      <label>物理源<select aria-label="物理源筛选" value={search.source ?? ''} onChange={event => change({ source: event.target.value as SceneSource || undefined })}>
        <option value="">全部物理源</option><option value="synthetic">Synthetic</option><option value="newton">Newton</option></select></label>
      <label>验证<select aria-label="可启动筛选" value={search.launchable === undefined ? '' : String(search.launchable)} onChange={event => change({ launchable: event.target.value === '' ? undefined : event.target.value === 'true' })}>
        <option value="">全部版本</option><option value="true">可启动</option><option value="false">不可启动</option></select></label>
      <div className="scene-view-toggle" role="group" aria-label="视图">{(['card', 'list'] as const).map(kind => <button key={kind} aria-pressed={view === kind} onClick={() => {
        setPreferred(kind); try { localStorage.setItem(viewKey, kind); } catch { /* optional preference */ } change({ view: kind });
      }}>{kind === 'card' ? '▦ 卡片' : '☷ 列表'}</button>)}</div>
    </section>
    {error ? <div role="alert" className="platform-error">{error} <button onClick={() => setAttempt(attempt + 1)}>重试</button></div> : !scenes ? <div role="status" className="scene-empty">正在读取场景库…</div> : scenes.length === 0 ? <div className="scene-empty"><h2>没有匹配的场景</h2><p>调整搜索或筛选条件。</p><button onClick={() => void navigate({ search: { view }, replace: true })}>清除筛选</button></div> :
      <SceneThumbnails scenes={scenes}>{thumbnails => view === 'card' ? <div className="scene-card-grid">{scenes.map((scene, index) =>
        <Link key={scene.id} to="/scenes/$name/$rev" params={{ name: scene.sceneId, rev: String(scene.rev) }} search={{ source: search.source }} className="scene-card">
          <div className="scene-thumbnail">{thumbnails(scene)}</div><div className="scene-card-caption"><b>图 {index + 1}</b><code>{scene.id}</code></div>
          <h2>{scene.title}</h2><div className="scene-card-origin">{scene.sources.map(sourceLabel).join(' / ')} · {rootLabel(scene.root)}</div>
          <div className="scene-card-meta"><LaunchStatus scene={scene} /><span title={scene.countsReason ?? '初始快照统计'}>{scene.entityCount ?? '—'} 实体 · {scene.deviceCount ?? '—'} 绑定</span></div>
          {scene.entityCount === null && <p className="scene-counts-reason">{scene.countsReason}</p>}
          <p className={scene.launchable ? 'scene-note' : 'scene-failure'}>{scene.launchReason ?? scene.description}</p>
        </Link>)}</div> : <div className="scene-table-wrap"><table className="scene-library-table"><thead><tr><th>预览</th><th>场景</th><th>物理源</th><th>构建 · 来源</th><th>实体 / 绑定</th><th>验证</th><th>备注</th></tr></thead><tbody>{scenes.map(scene => <tr key={scene.id}>
          <td><Link className="scene-mini-thumbnail" to="/scenes/$name/$rev" params={{ name: scene.sceneId, rev: String(scene.rev) }} search={{ source: search.source }} aria-label={`打开 ${scene.id}`}>{thumbnails(scene)}</Link></td>
          <td><Link to="/scenes/$name/$rev" params={{ name: scene.sceneId, rev: String(scene.rev) }} search={{ source: search.source }}><code>{scene.id}</code><small>{scene.title}</small></Link></td>
          <td>{scene.sources.join(' / ')}</td><td>{scene.stage.startsWith('python:') ? 'Python 构建' : scene.stage} · {rootLabel(scene.root)}</td>
          <td title={scene.countsReason ?? '初始快照统计'}>{scene.entityCount ?? '—'} / {scene.deviceCount ?? '—'}{scene.entityCount === null && <small>{scene.countsReason}</small>}</td><td><LaunchStatus scene={scene} /></td>
          <td className={scene.launchable ? 'scene-note' : 'scene-failure'}>{scene.launchReason ?? scene.description}</td></tr>)}</tbody></table></div>}</SceneThumbnails>}
  </main>;
}
