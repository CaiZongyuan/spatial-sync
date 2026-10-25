import { createFileRoute, Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { assetKinds, assetParams, dimensionsLabel, getAssetLibrary, type AssetKind, type AssetVersion } from '../lib/asset-library';
import { searchCatalog, type CatalogCandidate, type CatalogScope, type CatalogSearchFilters, type CatalogSearchResponse, type CatalogUsage } from '../lib/catalog';
import { AssetThumbnails } from '../components/AssetThumbnails';
import { AssetGapChips, CompletenessDots } from '../components/AssetCompleteness';
import { CatalogCandidateList, CatalogDetailPanel, CatalogSnapshotBar, CatalogZeroResult } from '../components/CatalogCandidates';
import { AcquisitionRecords } from '../components/AcquisitionPanel';
import { CatalogSyncPanel } from '../components/CatalogSyncPanel';

type Search = {
  scope?: CatalogScope; view?: 'card' | 'list'; kind?: AssetKind; usage?: CatalogUsage;
  gaps?: boolean; approximate?: boolean; q?: string;
};
const scopeLabels: Record<CatalogScope, string> = { local: '本地', online: '在线', all: '全部' };
export const Route = createFileRoute('/assets/')({
  validateSearch: (value: Record<string, unknown>): Search => ({
    scope: value.scope === 'local' || value.scope === 'online' || value.scope === 'all' ? value.scope : undefined,
    view: value.view === 'card' || value.view === 'list' ? value.view : undefined,
    kind: typeof value.kind === 'string' && Object.hasOwn(assetKinds, value.kind) ? value.kind as AssetKind : undefined,
    usage: value.usage === 'dressing' || value.usage === 'staticCollision' ? value.usage : undefined,
    gaps: value.gaps === true || value.gaps === 'true' ? true : undefined,
    approximate: value.approximate === true || value.approximate === 'true' ? true : undefined,
    q: typeof value.q === 'string' && value.q ? value.q : undefined,
  }), component: AssetLibrary,
});
function AssetLibrary() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const scope = search.scope ?? 'local';
  const [assets, setAssets] = useState<AssetVersion[]>();
  const [catalog, setCatalog] = useState<CatalogSearchResponse>();
  const [error, setError] = useState<string>();
  const [catalogError, setCatalogError] = useState<string>();
  const [more, setMore] = useState<{ loading: boolean; error?: string }>({ loading: false });
  const [detail, setDetail] = useState<CatalogCandidate>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (scope !== 'local') return;
    const controller = new AbortController();
    setAssets(undefined); setError(undefined);
    void getAssetLibrary({ q: search.q, kind: search.kind, gaps: search.gaps }, controller.signal)
      .then(value => { if (!controller.signal.aborted) setAssets(value.assets); })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [scope, search.kind, search.gaps, search.q, attempt]);
  useEffect(() => {
    if (scope === 'local') return;
    const controller = new AbortController();
    setCatalog(undefined); setCatalogError(undefined); setMore({ loading: false });
    const filters: CatalogSearchFilters = { q: search.q, scope, kind: search.kind, usage: search.usage, approximate: search.approximate, limit: 20 };
    void searchCatalog(filters, controller.signal)
      .then(value => { if (!controller.signal.aborted) setCatalog(value); })
      .catch((error: Error) => { if (!controller.signal.aborted) setCatalogError(error.message); });
    return () => controller.abort();
  }, [scope, search.kind, search.usage, search.approximate, search.q, attempt]);
  useEffect(() => { setDetail(undefined); }, [scope, search.kind, search.usage, search.approximate, search.q]);
  const change = (next: Partial<Search>) => void navigate({ search: previous => ({ ...previous, ...next }), replace: true });
  const actOnZero = (action: 'remove_filter' | 'broaden_scope' | 'enable_approximation', filter?: string) => {
    if (action === 'broaden_scope') return change({ scope: 'all' });
    if (action === 'enable_approximation') return change({ approximate: true });
    if (!filter) return void navigate({
      search: previous => ({ ...previous, q: undefined, kind: undefined, usage: undefined, approximate: undefined }), replace: true,
    });
    if (filter === 'kind') return change({ kind: undefined });
    if (filter === 'usage') return change({ usage: undefined });
  };
  const loadMore = async () => {
    if (!catalog?.nextCursor) return;
    setMore({ loading: true });
    try {
      const next = await searchCatalog({
        q: search.q, scope, kind: search.kind, usage: search.usage,
        approximate: search.approximate, limit: 20, cursor: catalog.nextCursor,
      });
      setCatalog(previous => previous?.snapshot.id === catalog.snapshot.id
        && previous.nextCursor === catalog.nextCursor
        ? { ...next, results: [...previous.results, ...next.results] } : previous);
      setMore({ loading: false });
    } catch (error) {
      setMore({ loading: false, error: (error as Error).message });
    }
  };
  const catalogToolbar = catalog
    ? `${catalog.results.length} / ${catalog.counts.matching} 个候选 · 在线 ${catalog.counts.onlineMatching} · 本地 ${catalog.counts.localMatching}${catalog.counts.approximate ? ` · 近似 ${catalog.counts.approximate}` : ''}`
    : '正在检索目录…';
  return <main className="asset-page asset-library-page">
    <header className="asset-page-heading"><h1>素材库</h1><p>不可变的版本化素材包：物理、碰撞、PBR 视觉、来源与许可证</p></header>
    <div className="asset-library-toolbar"><span>{scope === 'local' ? assets ? `${assets.length} 件素材 · 圆点依次为六项完整度` : '正在读取素材…' : catalogToolbar}</span>
      {scope === 'local' && <div className="asset-view-toggle" role="group" aria-label="视图">{(['card', 'list'] as const).map(view => <button key={view} aria-pressed={(search.view ?? 'card') === view} onClick={() => change({ view })}>{view === 'card' ? '▦ 卡片' : '☷ 列表'}</button>)}</div>}
    </div>
    {scope !== 'local' && <CatalogSyncPanel onPublished={() => setAttempt(value => value + 1)} />}
    <section className="asset-filters" aria-label="素材筛选">
      <div className="asset-scope-toggle" role="group" aria-label="范围">{(['local', 'online', 'all'] as const).map(value =>
        <button key={value} aria-pressed={scope === value} onClick={() => change({ scope: value })}>{scopeLabels[value]}</button>)}</div>
      <label>种类<select aria-label="素材种类" value={search.kind ?? ''} onChange={event => change({ kind: event.target.value as AssetKind || undefined })}><option value="">全部种类</option>{Object.entries(assetKinds).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label>
      {scope !== 'local' && <label>用途<select aria-label="用途" value={search.usage ?? ''} onChange={event => change({ usage: event.target.value as CatalogUsage || undefined })}><option value="">全部用途</option><option value="dressing">布景</option><option value="staticCollision">静态碰撞</option></select></label>}
      {scope === 'local' && <label className="asset-gaps"><input type="checkbox" checked={!!search.gaps} onChange={event => change({ gaps: event.target.checked || undefined })} />只看缺口</label>}
      {scope !== 'local' && <label className="asset-gaps"><input type="checkbox" checked={!!search.approximate} onChange={event => change({ approximate: event.target.checked || undefined })} />允许近似结果</label>}
      <label className="asset-search">搜索<input aria-label="搜索素材" type="search" placeholder={scope === 'local' ? '名称或 ID…' : '中英文名称、别名或精确 ID…'} value={search.q ?? ''} onChange={event => change({ q: event.target.value || undefined })} /></label>
    </section>
    {scope === 'local' ? (error ? <div className="asset-empty" role="alert">{error}<button onClick={() => setAttempt(attempt + 1)}>重试</button></div> : !assets ? <div className="asset-empty" role="status">正在读取素材库…</div> : !assets.length ? <div className="asset-empty"><h2>没有匹配的素材</h2><p>调整种类、缺口或搜索条件。</p><button onClick={() => void navigate({ search: { view: search.view }, replace: true })}>清除筛选</button></div> :
      <AssetThumbnails assets={assets}>{thumbnail => search.view !== 'list' ? <div className="asset-card-grid">{assets.map((asset, index) => <Link className="asset-card" key={asset.id} to="/assets/$kind/$name/$rev" params={assetParams(asset)}>
        <div className="asset-card-figure">{thumbnail(asset)}</div><div className="asset-figure-caption"><b>图 {index + 1}</b><code>{asset.id}</code></div><h2>{asset.title}</h2>
        <div className="asset-card-meta"><CompletenessDots asset={asset} /><AssetGapChips asset={asset} first /></div>
      </Link>)}</div> : <section className="asset-panel"><header><h2>素材 · {assets.length}</h2></header><div className="asset-table-wrap"><table className="asset-list-table"><thead><tr><th>预览</th><th>素材</th><th>种类</th><th>尺寸</th><th>实现类型</th><th>完整度</th><th>缺口</th></tr></thead><tbody>{assets.map(asset => <tr key={asset.id}>
        <td><Link className="asset-miniature" to="/assets/$kind/$name/$rev" params={assetParams(asset)} aria-label={`打开 ${asset.id}`}>{thumbnail(asset)}</Link></td>
        <td><Link to="/assets/$kind/$name/$rev" params={assetParams(asset)}><code>{asset.id}</code><small>{asset.title}</small></Link></td><td>{assetKinds[asset.kind]}</td><td>{dimensionsLabel(asset.dimensionsM)}</td><td>{asset.implementedTypes.join(' · ') || '无'}</td><td><CompletenessDots asset={asset} /></td><td><AssetGapChips asset={asset} /></td>
      </tr>)}</tbody></table></div></section>}</AssetThumbnails>) : catalogError ? <div className="asset-empty" role="alert">{catalogError}<button onClick={() => setAttempt(attempt + 1)}>重试</button></div>
      : !catalog ? <div className="asset-empty" role="status">正在检索目录…</div>
        : <>
          <CatalogSnapshotBar snapshot={catalog.snapshot} semantic={catalog.semantic} />
          {catalog.interpretedQuery.notes.length > 0 && <ul className="catalog-notes" aria-label="查询说明">{catalog.interpretedQuery.notes.map(note => <li key={note}>{note}</li>)}</ul>}
          {catalog.zeroResult ? <CatalogZeroResult response={catalog} onAction={actOnZero} />
            : <CatalogCandidateList response={catalog} onLoadMore={() => void loadMore()} loadingMore={more.loading} loadMoreError={more.error} onDetail={setDetail} />}
        </>}
    {scope !== 'local' && <AcquisitionRecords />}
    {detail && <CatalogDetailPanel candidate={detail} onClose={() => setDetail(undefined)} />}
  </main>;
}
