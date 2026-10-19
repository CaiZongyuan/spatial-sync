import { createFileRoute, Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { assetKinds, assetParams, dimensionsLabel, gapLabel, getAssetLibrary, type AssetKind, type AssetVersion } from '../lib/asset-library';
import { AssetThumbnails } from '../components/AssetThumbnails';
import { CompletenessDots } from '../components/AssetCompleteness';

type Search = { view?: 'card' | 'list'; kind?: AssetKind; gaps?: boolean; q?: string };
export const Route = createFileRoute('/assets/')({
  validateSearch: (value: Record<string, unknown>): Search => ({
    view: value.view === 'card' || value.view === 'list' ? value.view : undefined,
    kind: typeof value.kind === 'string' && value.kind in assetKinds ? value.kind as AssetKind : undefined,
    gaps: value.gaps === true || value.gaps === 'true' ? true : undefined,
    q: typeof value.q === 'string' && value.q ? value.q : undefined,
  }), component: AssetLibrary,
});
function AssetLibrary() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  const [assets, setAssets] = useState<AssetVersion[]>();
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(undefined);
    void getAssetLibrary(search, controller.signal).then(value => { if (!controller.signal.aborted) setAssets(value.assets); })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [search.kind, search.gaps, search.q, attempt]);
  const change = (next: Partial<Search>) => void navigate({ search: previous => ({ ...previous, ...next }), replace: true });
  return <main className="asset-page asset-library-page">
    <header className="asset-page-heading"><h1>素材库</h1><p>不可变的版本化素材包：物理、碰撞、PBR 视觉、来源与许可证</p></header>
    <div className="asset-library-toolbar"><span>{assets ? `${assets.length} 件素材 · 圆点依次为六项完整度` : '正在读取素材…'}</span>
      <div className="asset-view-toggle" role="group" aria-label="视图">{(['card', 'list'] as const).map(view => <button key={view} aria-pressed={(search.view ?? 'card') === view} onClick={() => change({ view })}>{view === 'card' ? '▦ 卡片' : '☷ 列表'}</button>)}</div>
    </div>
    <section className="asset-filters" aria-label="素材筛选">
      <label>种类<select aria-label="素材种类" value={search.kind ?? ''} onChange={event => change({ kind: event.target.value as AssetKind || undefined })}><option value="">全部种类</option>{Object.entries(assetKinds).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label>
      <label className="asset-gaps"><input type="checkbox" checked={!!search.gaps} onChange={event => change({ gaps: event.target.checked || undefined })} />只看缺口</label>
      <label className="asset-search">搜索<input aria-label="搜索素材" type="search" placeholder="名称或 ID…" value={search.q ?? ''} onChange={event => change({ q: event.target.value || undefined })} /></label>
    </section>
    {error ? <div className="asset-empty" role="alert">{error}<button onClick={() => setAttempt(attempt + 1)}>重试</button></div> : !assets ? <div className="asset-empty" role="status">正在读取素材库…</div> : !assets.length ? <div className="asset-empty"><h2>没有匹配的素材</h2><p>调整种类、缺口或搜索条件。</p><button onClick={() => void navigate({ search: { view: search.view }, replace: true })}>清除筛选</button></div> :
      <AssetThumbnails assets={assets}>{thumbnail => search.view !== 'list' ? <div className="asset-card-grid">{assets.map((asset, index) => <Link className="asset-card" key={asset.id} to="/assets/$kind/$name/$rev" params={assetParams(asset)}>
        <div className="asset-card-figure">{thumbnail(asset)}</div><div className="asset-figure-caption"><b>图 {index + 1}</b><code>{asset.id}</code></div><h2>{asset.title}</h2>
        <div className="asset-card-meta"><CompletenessDots asset={asset} />{asset.gaps[0] && <span className="asset-chip is-warning"><i />{gapLabel(asset.gaps[0])}</span>}</div>
      </Link>)}</div> : <section className="asset-panel"><header><h2>素材 · {assets.length}</h2></header><div className="asset-table-wrap"><table className="asset-list-table"><thead><tr><th>预览</th><th>素材</th><th>种类</th><th>尺寸</th><th>实现类型</th><th>完整度</th><th>缺口</th></tr></thead><tbody>{assets.map(asset => <tr key={asset.id}>
        <td><Link className="asset-miniature" to="/assets/$kind/$name/$rev" params={assetParams(asset)} aria-label={`打开 ${asset.id}`}>{thumbnail(asset)}</Link></td>
        <td><Link to="/assets/$kind/$name/$rev" params={assetParams(asset)}><code>{asset.id}</code><small>{asset.title}</small></Link></td><td>{assetKinds[asset.kind]}</td><td>{dimensionsLabel(asset.dimensionsM)}</td><td>{asset.implementedTypes.join(' · ') || '无'}</td><td><CompletenessDots asset={asset} /></td><td>{asset.gaps.map(gap => <span key={gap} className="asset-gap-label">{gapLabel(gap)}</span>)}</td>
      </tr>)}</tbody></table></div></section>}</AssetThumbnails>}
  </main>;
}
