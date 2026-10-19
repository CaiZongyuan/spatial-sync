import { completenessLabels, gapLabel, statusLabels, type AssetVersion } from '../lib/asset-library';

export function AssetGapChips({ asset, first = false }: { asset: AssetVersion; first?: boolean }) {
  return <span className="asset-gap-chips">{(first ? asset.gaps.slice(0, 1) : asset.gaps).map(gap => {
    const status = asset.completeness.items.find(item => item.id === gap)?.status ?? 'warning';
    return <span key={gap} className={`asset-chip is-${status}`}><i />{gapLabel(gap)}</span>;
  })}</span>;
}

export function CompletenessDots({ asset }: { asset: AssetVersion }) {
  const description = asset.completeness.items.map(item => `${completenessLabels[item.id] ?? item.id}：${statusLabels[item.status]}`).join('，');
  return <span className="asset-dots" role="img" aria-label={description} title={description}>
    {asset.completeness.items.map(item => <i key={item.id} className={`is-${item.status}`} />)}
  </span>;
}
export function AssetCompleteness({ asset }: { asset: AssetVersion }) {
  return <ul className="asset-completeness" aria-label="六项完整度">
    {asset.completeness.items.map(item => <li key={item.id} data-status={item.status}>
      <span>{completenessLabels[item.id] ?? item.id}</span><span className={`asset-chip is-${item.status}`}><i />{statusLabels[item.status]}</span><small>{item.message}</small>
    </li>)}
  </ul>;
}
