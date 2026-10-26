import { createFileRoute, Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { assetKinds, assetParams, dimensionsLabel, getAssetLibrary, type AssetKind, type AssetVersion } from '../lib/asset-library';
import { searchCatalog, type CatalogCandidate, type CatalogScope, type CatalogSearchFilters, type CatalogSearchResponse, type CatalogUsage } from '../lib/catalog';
import { AssetThumbnails } from '../components/AssetThumbnails';
import { AssetGapChips, CompletenessDots } from '../components/AssetCompleteness';
import { CatalogCandidateList, CatalogDetailPanel, CatalogSnapshotBar, CatalogZeroResult } from '../components/CatalogCandidates';
import { AcquisitionRecords } from '../components/AcquisitionPanel';
import { CatalogSyncPanel } from '../components/CatalogSyncPanel';
import { CatalogMaintenancePanel } from '../components/CatalogMaintenancePanel';
import { getCatalogVocabulary, type CatalogVocabulary, type MetadataCollection } from '../lib/catalog-maintenance';

type Search = {
  scope?: CatalogScope; view?: 'card' | 'list'; kind?: AssetKind; usage?: CatalogUsage;
  gaps?: boolean; approximate?: boolean; q?: string; provider?: string;
  category?: string; tag?: string; sort?: 'relevance' | 'name' | 'sourceBytes';
  page?: number; pageSize?: number; release?: string;
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
    provider: typeof value.provider === 'string' && /^[a-z0-9][a-z0-9-]{0,99}$/.test(value.provider) ? value.provider : undefined,
    category: typeof value.category === 'string' && value.category.length <= 100 ? value.category || undefined : undefined,
    tag: typeof value.tag === 'string' && value.tag.length <= 100 ? value.tag || undefined : undefined,
    sort: value.sort === 'name' || value.sort === 'sourceBytes' || value.sort === 'relevance' ? value.sort : undefined,
    page: Number.isInteger(Number(value.page)) && Number(value.page) > 0 && Number(value.page) <= 100000 ? Number(value.page) : undefined,
    pageSize: [12, 24, 48].includes(Number(value.pageSize)) ? Number(value.pageSize) : undefined,
    release: typeof value.release === 'string' && /^sha256:[a-f0-9]{64}$/.test(value.release) ? value.release : undefined,
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
  const [detail, setDetail] = useState<CatalogCandidate>();
  const [attempt, setAttempt] = useState(0);
  const [sources, setSources] = useState<{ provider: string; dataset: string; entryCount: number }[]>([]);
  const [collections, setCollections] = useState<MetadataCollection[]>([]);
  const [vocabulary, setVocabulary] = useState<CatalogVocabulary>();
  const [newPublication, setNewPublication] = useState(false);
  const pageSize = search.pageSize ?? 24;
  const currentPage = search.page ?? 1;
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
    setCatalog(undefined); setCatalogError(undefined);
    const filters: CatalogSearchFilters = {
      q: search.q, scope, provider: search.provider, kind: search.kind, usage: search.usage,
      approximate: search.approximate, limit: pageSize, page: currentPage, sort: search.sort,
      category: search.category, tag: search.tag, release: search.release,
    };
    void searchCatalog(filters, controller.signal)
      .then(value => {
        if (controller.signal.aborted) return;
        setCatalog(value);
        if (!search.release && value.snapshot.id) void navigate({
          search: previous => ({ ...previous, release: value.snapshot.id!, page: currentPage, pageSize }), replace: true,
        });
      })
      .catch((error: Error) => { if (!controller.signal.aborted) setCatalogError(error.message); });
    return () => controller.abort();
  }, [scope, search.provider, search.kind, search.usage, search.approximate, search.q, search.category, search.tag, search.sort, search.release, currentPage, pageSize, attempt, navigate]);
  useEffect(() => { setDetail(undefined); }, [scope, search.provider, search.kind, search.usage, search.approximate, search.q, search.category, search.tag, search.release, currentPage]);
  useEffect(() => {
    if (scope === 'local') return;
    const controller = new AbortController();
    void getCatalogVocabulary(catalog?.snapshot.metadataRelease?.vocabularyVersion, controller.signal)
      .then(value => { if (!controller.signal.aborted) setVocabulary(value); }).catch(() => undefined);
    return () => controller.abort();
  }, [scope, catalog?.snapshot.metadataRelease?.vocabularyVersion]);
  const change = (next: Partial<Search>) => void navigate({ search: previous => ({
    ...previous, ...next, page: next.page ?? (Object.keys(next).every(key => key === 'view') ? previous.page : 1),
  }), replace: Object.keys(next).every(key => key === 'q' || key === 'tag') });
  const refresh = () => { setNewPublication(false); change({ release: undefined, page: 1 }); setAttempt(value => value + 1); };
  const actOnZero = (action: 'remove_filter' | 'broaden_scope' | 'enable_approximation', filter?: string) => {
    if (action === 'broaden_scope') return change({ scope: 'all' });
    if (action === 'enable_approximation') return change({ approximate: true });
    if (!filter) return void navigate({
      search: previous => ({ ...previous, q: undefined, kind: undefined, usage: undefined, provider: undefined, category: undefined, tag: undefined, approximate: undefined, page: 1 }), replace: true,
    });
    if (filter === 'kind') return change({ kind: undefined });
    if (filter === 'provider') return change({ provider: undefined });
    if (filter === 'usage') return change({ usage: undefined });
    if (filter === 'category') return change({ category: undefined });
    if (filter === 'tag') return change({ tag: undefined });
  };
  const totalPages = scope === 'local' ? Math.max(1, Math.ceil((assets?.length ?? 0) / pageSize)) : catalog?.pagination?.totalPages ?? 1;
  const localAssets = assets?.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const sourceOptions = [...sources, ...collections.filter(collection => !sources.some(source => source.provider === collection.id))
    .map(collection => ({ provider: collection.id, dataset: collection.dataset, entryCount: 0 }))];
  const catalogToolbar = catalog
    ? `${catalog.results.length} / ${catalog.counts.matching} 个候选 · 在线 ${catalog.counts.onlineMatching} · 本地 ${catalog.counts.localMatching}${catalog.counts.approximate ? ` · 近似 ${catalog.counts.approximate}` : ''}`
    : '正在检索目录…';
  return <main className="asset-page asset-library-page">
    <header className="asset-page-heading"><h1>素材库</h1><p>不可变的版本化素材包：物理、碰撞、PBR 视觉、来源与许可证</p></header>
    <div className="asset-library-toolbar"><span>{scope === 'local' ? assets ? `${assets.length} 件素材 · 圆点依次为六项完整度` : '正在读取素材…' : catalogToolbar}</span>
      <div className="asset-view-toggle" role="group" aria-label="视图">{(['card', 'list'] as const).map(view => <button key={view} aria-pressed={(search.view ?? 'card') === view} onClick={() => change({ view })}>{view === 'card' ? '▦ 卡片' : '☷ 列表'}</button>)}</div>
    </div>
    {scope !== 'local' && <>
      <CatalogSyncPanel provider={search.provider} onSources={setSources} onPublished={() => setNewPublication(true)} />
      <CatalogMaintenancePanel onCollections={setCollections} onPublication={() => setNewPublication(true)} />
      {newPublication && <p className="catalog-update-notice" role="status">有新目录可用。当前分页保持原发布。<button onClick={refresh}>查看最新目录</button></p>}
    </>}
    <section className="asset-filters" aria-label="素材筛选">
      <div className="asset-scope-toggle" role="group" aria-label="范围">{(['local', 'online', 'all'] as const).map(value =>
        <button key={value} aria-pressed={scope === value} onClick={() => change({ scope: value })}>{scopeLabels[value]}</button>)}</div>
      <label>种类<select aria-label="素材种类" value={search.kind ?? ''} onChange={event => change({ kind: event.target.value as AssetKind || undefined })}><option value="">全部种类</option>{Object.entries(assetKinds).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label>
      {scope !== 'local' && <label>来源<select aria-label="素材来源" value={search.provider ?? ''} onChange={event => change({ provider: event.target.value || undefined })}>
        <option value="">全部来源</option>{sourceOptions.map(source => <option key={source.provider} value={source.provider}>{source.provider}</option>)}
      </select></label>}
      {scope !== 'local' && <label>用途<select aria-label="用途" value={search.usage ?? ''} onChange={event => change({ usage: event.target.value as CatalogUsage || undefined })}><option value="">全部用途</option><option value="dressing">布景</option><option value="staticCollision">静态碰撞</option></select></label>}
      {scope !== 'local' && <>
        <label>物体类别<select aria-label="物体类别" value={search.category ?? ''} onChange={event => change({ category: event.target.value || undefined })}>
          <option value="">全部类别</option>{search.category && !vocabulary?.categories.some(category => category.id === search.category) && <option value={search.category}>{search.category}</option>}
          {vocabulary?.categories.map(category => <option key={category.id} value={category.id}>{category.name.zh || category.name.en}</option>)}
        </select></label>
        <label>标签<input aria-label="素材标签" list="catalog-tag-options" value={search.tag ?? ''} onChange={event => change({ tag: event.target.value || undefined })} placeholder="标签或用途…" /></label>
        <datalist id="catalog-tag-options">{vocabulary?.tags.map(tag => <option key={tag.id} value={tag.id}>{tag.name.zh || tag.name.en}</option>)}</datalist>
        <label>排序<select aria-label="素材排序" value={search.sort ?? 'relevance'} onChange={event => change({ sort: event.target.value as Search['sort'] })}>
          <option value="relevance">相关度</option><option value="name">名称</option><option value="sourceBytes">来源大小</option>
        </select></label>
      </>}
      {scope === 'local' && <label className="asset-gaps"><input type="checkbox" checked={!!search.gaps} onChange={event => change({ gaps: event.target.checked || undefined })} />只看缺口</label>}
      {scope !== 'local' && <label className="asset-gaps"><input type="checkbox" checked={!!search.approximate} onChange={event => change({ approximate: event.target.checked || undefined })} />允许近似结果</label>}
      <label className="asset-search">搜索<input aria-label="搜索素材" type="search" placeholder={scope === 'local' ? '名称或 ID…' : '中英文名称、别名或精确 ID…'} value={search.q ?? ''} onChange={event => change({ q: event.target.value || undefined })} /></label>
    </section>
    {scope === 'local' ? (error ? <div className="asset-empty" role="alert">{error}<button onClick={() => setAttempt(attempt + 1)}>重试</button></div> : !assets ? <div className="asset-empty" role="status">正在读取素材库…</div> : !assets.length ? <div className="asset-empty"><h2>没有匹配的素材</h2><p>调整种类、缺口或搜索条件。</p><button onClick={() => void navigate({ search: { view: search.view }, replace: true })}>清除筛选</button></div> :
      <AssetThumbnails assets={localAssets!}>{thumbnail => search.view !== 'list' ? <div className="asset-card-grid">{localAssets!.map((asset, index) => <Link className="asset-card" key={asset.id} to="/assets/$kind/$name/$rev" params={assetParams(asset)}>
        <div className="asset-card-figure">{thumbnail(asset)}</div><div className="asset-figure-caption"><b>图 {index + 1}</b><code>{asset.id}</code></div><h2>{asset.title}</h2>
        <div className="asset-card-meta"><CompletenessDots asset={asset} /><AssetGapChips asset={asset} first /></div>
      </Link>)}</div> : <section className="asset-panel"><header><h2>素材 · {assets.length}</h2></header><div className="asset-table-wrap"><table className="asset-list-table"><thead><tr><th>预览</th><th>素材</th><th>种类</th><th>尺寸</th><th>实现类型</th><th>完整度</th><th>缺口</th></tr></thead><tbody>{localAssets!.map(asset => <tr key={asset.id}>
        <td><Link className="asset-miniature" to="/assets/$kind/$name/$rev" params={assetParams(asset)} aria-label={`打开 ${asset.id}`}>{thumbnail(asset)}</Link></td>
        <td><Link to="/assets/$kind/$name/$rev" params={assetParams(asset)}><code>{asset.id}</code><small>{asset.title}</small></Link></td><td>{assetKinds[asset.kind]}</td><td>{dimensionsLabel(asset.dimensionsM)}</td><td>{asset.implementedTypes.join(' · ') || '无'}</td><td><CompletenessDots asset={asset} /></td><td><AssetGapChips asset={asset} /></td>
    </tr>)}</tbody></table></div></section>}</AssetThumbnails>) : catalogError ? <div className="asset-empty" role="alert">{catalogError}<button onClick={() => setAttempt(attempt + 1)}>重试</button><button onClick={refresh}>重新查询最新目录</button></div>
      : !catalog ? <div className="asset-empty" role="status">正在检索目录…</div>
        : <>
          <CatalogSnapshotBar snapshot={catalog.snapshot} semantic={catalog.semantic} />
          {catalog.interpretedQuery.notes.length > 0 && <ul className="catalog-notes" aria-label="查询说明">{catalog.interpretedQuery.notes.map(note => <li key={note}>{note}</li>)}</ul>}
          {catalog.zeroResult ? <CatalogZeroResult response={catalog} onAction={actOnZero} />
            : <CatalogCandidateList response={catalog} view={search.view} onDetail={setDetail} />}
        </>}
    {((scope === 'local' && assets?.length) || (scope !== 'local' && catalog && !catalog.zeroResult)) ? <nav className="catalog-pagination" aria-label="素材分页">
      <button disabled={currentPage <= 1} onClick={() => change({ page: currentPage - 1 })}>上一页</button>
      <span>第 {currentPage} / {totalPages} 页</span>
      <button disabled={currentPage >= totalPages} onClick={() => change({ page: currentPage + 1 })}>下一页</button>
      <form onSubmit={event => { event.preventDefault(); const input = event.currentTarget.elements.namedItem('page') as HTMLInputElement; const page = Number(input.value); if (Number.isInteger(page) && page >= 1 && page <= totalPages) change({ page }); }}>
        <label>跳至<input key={currentPage} aria-label="页位置" name="page" type="number" min="1" max={totalPages} defaultValue={currentPage} /></label><button type="submit">前往</button>
      </form>
      <label>每页<select aria-label="每页条目" value={pageSize} onChange={event => change({ pageSize: Number(event.target.value) })}>{[12, 24, 48].map(size => <option value={size} key={size}>{size} 项</option>)}</select></label>
      {catalog?.counts.budgetTruncated && scope !== 'local' && <small>长内容按响应限额分页</small>}
    </nav> : null}
    {scope !== 'local' && <AcquisitionRecords />}
    {detail && <CatalogDetailPanel candidate={detail} release={catalog?.snapshot.id ?? undefined} onClose={() => setDetail(undefined)} />}
  </main>;
}
