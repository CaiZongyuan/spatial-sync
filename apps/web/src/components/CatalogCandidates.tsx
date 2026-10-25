import { useEffect, useState } from 'react';
import { Link } from '@tanstack/react-router';
import {
  filterLabels, formatAge, formatBytes, getCatalogEntry, getCatalogEstimate, semanticLabel,
  shortRevision, usageLabel, usageLabels, usageState, zeroReasonLabels,
  type CatalogCandidate, type CatalogEntryDocument, type CatalogEstimateDocument,
  type CatalogSearchResponse, type CatalogSemantic, type CatalogSnapshot,
} from '../lib/catalog';
import { assetKinds, dimensionsLabel } from '../lib/asset-library';
import { AcquisitionStart } from './AcquisitionPanel';

const attributeLabels: Record<string, string> = {
  dimensionsM: '尺寸', dressing: '布景', staticCollision: '静态碰撞', license: '许可',
  provider: '来源', sourceBytes: '来源字节', usage: '用途', kind: '种类',
};

const labelFor = (id: string) => filterLabels[id] ?? attributeLabels[id] ?? id;
const imageUrl = (url: string | null | undefined) =>
  url && /\.(png|jpe?g|webp)(\?.*)?$/i.test(url) ? url : null;

function chips(values: string[]) {
  return values.map(value => <span key={value} className="asset-chip is-warning"><i />{labelFor(value)}</span>);
}

export function CatalogSnapshotBar({ snapshot, semantic }: {
  snapshot: CatalogSnapshot; semantic?: CatalogSemantic;
}) {
  return <section className="catalog-snapshot" aria-label="目录快照">
    <span className="catalog-snapshot-item">快照 <code title={snapshot.id ?? undefined}>{snapshot.id ?? '不可用'}</code></span>
    <span className="catalog-snapshot-item">索引时间 <span>{snapshot.indexedAt ?? '未知'}</span></span>
    <span className="catalog-snapshot-item">新鲜度 <b>{formatAge(snapshot.ageSeconds)}</b> · {snapshot.entryCount} 条 · {snapshot.coverage ?? '覆盖范围未知'}</span>
    <span className="catalog-snapshot-item">{snapshot.offlineReadable ? '快照可离线读取' : '快照不可离线读取'} · {snapshot.networkRequired ? '检索需要网络' : '检索不联网'}</span>
    {semantic && <span className="catalog-snapshot-item" title={semantic.notice}>{semanticLabel(semantic)}{semantic.applied ? ` · 本次召回 ${semantic.hits} 条` : ''}</span>}
  </section>;
}

export function CatalogZeroResult({ response, onAction }: {
  response: CatalogSearchResponse;
  onAction: (action: 'remove_filter' | 'broaden_scope' | 'enable_approximation', filter?: string) => void;
}) {
  const zero = response.zeroResult!;
  return <div className="catalog-zero">
    <h2>没有匹配的候选</h2>
    <p className="catalog-zero-reason"><span className="asset-chip is-warning"><i />{zeroReasonLabels[zero.reason] ?? zero.reason}</span></p>
    <ul className="catalog-zero-messages">{zero.messages.map(message => <li key={message}>{message}</li>)}</ul>
    {zero.excludedBy.length > 0 && <div className="catalog-zero-excluded">
      <h3>被排除的候选</h3>
      <ul>{zero.excludedBy.map(item => <li key={item.filter}>{labelFor(item.filter)}：{item.count} 个候选</li>)}</ul>
    </div>}
    <div className="catalog-zero-actions">
      {zero.suggestions.map(suggestion => suggestion.action === 'remove_filter'
        ? <button key={`remove:${suggestion.filter}`} onClick={() => onAction('remove_filter', suggestion.filter)}>移除条件：{labelFor(suggestion.filter ?? '')}</button>
        : suggestion.action === 'broaden_scope'
          ? <button key="broaden" onClick={() => onAction('broaden_scope')}>扩大范围到全部</button>
          : <button key="approximate" onClick={() => onAction('enable_approximation')}>允许近似结果</button>)}
      <button className="catalog-zero-clear" onClick={() => onAction('remove_filter')}>清除查询与条件</button>
    </div>
    <p className="asset-small-note">条件不会自动放宽；以上操作会更新地址栏中的查询状态。</p>
  </div>;
}

function CandidatePreview({ candidate }: { candidate: CatalogCandidate }) {
  const localUrl = imageUrl(candidate.local?.asset.thumbnailUrl) ?? imageUrl(candidate.preview.url);
  if ((candidate.preview.kind === 'local' || candidate.preview.kind === 'cached') && localUrl)
    return <img src={localUrl} alt={`${candidate.title} 的缓存预览`} loading="lazy" />;
  return <div className="catalog-preview-placeholder" role="img" aria-label="在线预览未缓存">
    <b>预览未缓存</b>
    <small>{candidate.preview.reason ?? '获取流程会解析来源预览；这里不会请求远程图片。'}</small>
  </div>;
}

function OnlineCandidateCard({ candidate, index, onDetail }: {
  candidate: CatalogCandidate; index: number; onDetail: () => void;
}) {
  const approximate = candidate.match.unsatisfied.length > 0 || candidate.match.unknown.length > 0;
  const secondaryName = candidate.title === candidate.names.zh ? candidate.names.en : candidate.names.zh;
  return <article className="catalog-card is-online" data-candidate={candidate.id}>
    <div className="catalog-card-figure"><CandidatePreview candidate={candidate} /></div>
    <div className="asset-figure-caption"><b>图 {index + 1} · 在线候选</b><code>{candidate.source.provider}/{candidate.source.packageId}</code></div>
    <h2 className="catalog-card-title">{candidate.title} {secondaryName && secondaryName !== candidate.title ? <small>{secondaryName}</small> : null}</h2>
    <p className="catalog-description">{candidate.descriptions.zh || candidate.descriptions.en}</p>
    <dl className="catalog-facts">
      <div><dt>来源</dt><dd>{candidate.source.provider}/{candidate.source.packageId} · <span title={candidate.source.revision ?? undefined}>{shortRevision(candidate.source.revision)}</span></dd></div>
      <div><dt>许可</dt><dd>{candidate.license.name ?? '未知'} <span className={`asset-chip is-${candidate.license.unknown ? 'not_applicable' : candidate.license.admitted ? 'present' : 'missing'}`}><i />{candidate.license.unknown ? '未审查／上游声明' : candidate.license.admitted ? '已准入' : '未准入'}</span></dd></div>
      <div><dt>用途</dt><dd className="catalog-usage">{(Object.keys(usageLabels) as (keyof typeof usageLabels)[]).map(name => <span key={name} className={`asset-chip is-${usageState(candidate.usage[name])}`}><i />{usageLabels[name]} {usageLabel(candidate.usage[name])}</span>)}</dd></div>
      <div><dt>本地版本</dt><dd>{candidate.installed.versions.length
        ? candidate.installed.versions.map(version => <code key={version.id}>{version.id}</code>)
        : '尚未安装本地版本'}</dd></div>
      <div><dt>获取</dt><dd><span className={`asset-chip is-${candidate.acquisition.eligible ? 'present' : 'missing'}`}><i />{candidate.acquisition.eligible ? '可获取' : '不可获取'}</span>
        {candidate.acquisition.reasons.map(reason => <small key={reason} className="catalog-reason">{reason}</small>)}</dd></div>
    </dl>
    {candidate.match.reasons.length > 0 && <p className="catalog-match">匹配：{candidate.match.reasons.map(reason => `${reason.field}「${reason.value}」`).join('，')}</p>}
    {(approximate || candidate.unknown.length > 0) && <div className="catalog-candidate-warnings">
      {approximate && <span className="asset-chip is-warning"><i />近似</span>}
      {candidate.match.unsatisfied.length > 0 && <p><b>不满足</b>{chips(candidate.match.unsatisfied)}</p>}
      {candidate.match.unknown.length > 0 && <p><b>未知条件</b>{chips(candidate.match.unknown)}</p>}
      {candidate.unknown.length > 0 && <p><b>未知属性</b>{chips(candidate.unknown)}</p>}
    </div>}
    <button className="catalog-detail-toggle" onClick={onDetail}>详情与估算</button>
  </article>;
}

function LocalCandidateCard({ candidate, index }: { candidate: CatalogCandidate; index: number }) {
  const asset = candidate.local!.asset;
  const kind = asset.kind ?? candidate.kind;
  const name = candidate.local!.id.split('/')[1]?.split('@')[0] ?? '';
  const thumbnail = imageUrl(asset.thumbnailUrl);
  return <article className="catalog-card is-local" data-candidate={candidate.id}>
    <div className="catalog-card-figure">{thumbnail
      ? <img src={thumbnail} alt={`${candidate.title} 的本地预览`} loading="lazy" />
      : <div className="catalog-preview-placeholder" role="img" aria-label="本地预览不可用"><b>无本地预览</b><small>{candidate.preview.reason ?? '该版本没有记录缩略图。'}</small></div>}</div>
    <div className="asset-figure-caption"><b>图 {index + 1} · 本地结果</b><code>{candidate.id}</code></div>
    <h2 className="catalog-card-title">{candidate.title} <small>{asset.title !== candidate.title ? asset.title : null}</small></h2>
    <dl className="catalog-facts">
      <div><dt>种类</dt><dd>{kind && kind in assetKinds ? assetKinds[kind as keyof typeof assetKinds] : kind}</dd></div>
      <div><dt>尺寸</dt><dd>{dimensionsLabel(asset.dimensionsM)}</dd></div>
      <div><dt>实现类型</dt><dd>{asset.implementedTypes.join(' · ') || '无'}</dd></div>
      <div><dt>完整度</dt><dd>{asset.gaps.length ? `${asset.gaps.length} 项缺口` : '无缺口'}</dd></div>
    </dl>
    <Link className="catalog-detail-toggle" to="/assets/$kind/$name/$rev" params={{ kind, name, rev: String(candidate.local!.rev) }}>打开本地素材</Link>
  </article>;
}

export function CatalogCandidateList({ response, onLoadMore, loadingMore, loadMoreError, onDetail }: {
  response: CatalogSearchResponse;
  onLoadMore: () => void;
  loadingMore: boolean;
  loadMoreError?: string;
  onDetail: (candidate: CatalogCandidate) => void;
}) {
  return <>
    <div className="catalog-result-grid">{response.results.map((candidate, index) => candidate.type === 'online'
      ? <OnlineCandidateCard key={`online:${candidate.id}`} candidate={candidate} index={index} onDetail={() => onDetail(candidate)} />
      : <LocalCandidateCard key={`local:${candidate.id}`} candidate={candidate} index={index} />)}</div>
    {response.nextCursor && <div className="catalog-more"><button onClick={onLoadMore} disabled={loadingMore}>{loadingMore ? '正在加载…' : '加载更多'}</button>{loadMoreError && <span role="alert">{loadMoreError}</span>}</div>}
    {!response.nextCursor && (response.truncated || response.counts.budgetTruncated) && <p className="asset-small-note">结果受响应大小限制而截断；请缩小查询范围。</p>}
  </>;
}

type DetailState = {
  loading: boolean;
  error?: string;
  entry?: CatalogEntryDocument;
  estimate?: CatalogEstimateDocument;
};

export function CatalogDetailPanel({ candidate, onClose }: { candidate: CatalogCandidate; onClose: () => void }) {
  const [state, setState] = useState<DetailState>({ loading: true });
  const { provider, packageId, revision } = candidate.source;
  useEffect(() => {
    const controller = new AbortController();
    setState({ loading: true });
    if (!provider || !packageId || !revision) {
      setState({ loading: false, error: '候选缺少固定来源版本，无法读取详情。' });
      return () => controller.abort();
    }
    void Promise.all([
      getCatalogEntry(provider, packageId, revision, 'full', controller.signal),
      getCatalogEstimate(provider, packageId, revision, controller.signal),
    ]).then(([entry, estimate]) => { if (!controller.signal.aborted) setState({ loading: false, entry, estimate }); })
      .catch((error: Error) => { if (!controller.signal.aborted) setState({ loading: false, error: error.message }); });
    return () => controller.abort();
  }, [candidate.id, provider, packageId, revision]);
  const entry = state.entry;
  const estimate = state.estimate;
  return <section className="catalog-detail" aria-label="候选详情与估算">
    <header className="catalog-detail-heading">
      <div><h2>候选详情与估算</h2><p>{candidate.names.zh ?? candidate.title} · {candidate.names.en ?? ''} <code>{candidate.ref}</code></p></div>
      <button onClick={onClose}>关闭详情</button>
    </header>
    {entry?.previewTruncated && <p className="asset-small-note">部分长证据列表已按响应预算裁剪；不表示来源没有更多条目。</p>}
    {state.loading ? <p className="asset-empty" role="status">正在读取目录详情与估算…</p>
      : state.error ? <div className="asset-empty" role="alert">{state.error}</div>
        : entry && estimate ? <div className="catalog-detail-grid">
          <div className="catalog-detail-stack">
            <section className="asset-panel"><header><h2>身份、许可与用途</h2></header><div className="asset-panel-body">
              <dl className="asset-kv">
                <div><dt>目录引用</dt><dd><code>{entry.ref}</code></dd></div>
                <div><dt>固定来源版本</dt><dd><code>{entry.revision}</code></dd></div>
                <div><dt>上游包</dt><dd>{entry.provider}/{entry.packageId} · {entry.kind}</dd></div>
                <div><dt>来源文件</dt><dd>{formatBytes(entry.source.bytes)} · {entry.source.files ?? '未知'} 个文件</dd></div>
                <div><dt>来源链接</dt><dd><a className="catalog-source-link" href={entry.source.url} target="_blank" rel="noreferrer">{entry.source.url}</a></dd></div>
                <div><dt>BOM 外文件</dt><dd>{entry.source.bomItems === null ? '未知' : `${entry.source.bomNotInBom.length} 项`}<small className="catalog-reason">{entry.source.bomNotInBom.join('、')}</small></dd></div>
                <div><dt>许可</dt><dd>{entry.license.name} · {entry.license.admitted ? '已准入' : '未准入'}{entry.license.obligations.length ? ` · ${entry.license.obligations.join('、')}` : ''}</dd></div>
                <div><dt>许可证据</dt><dd><code>{entry.license.evidence.file ?? '尚未审查；声明不等于获取准入'}</code><small className="catalog-hash">{entry.license.evidence.sha256}</small></dd></div>
                <div><dt>附件许可</dt><dd className="catalog-evidence-list">{entry.license.attachments.length
                  ? entry.license.attachments.map(item => <span key={item.file}><code>{item.file}</code> {item.license}</span>)
                  : '无'}</dd></div>
                <div><dt>适配状态</dt><dd>{entry.adaptation.status} · {entry.adaptation.dimensionsM ? dimensionsLabel(entry.adaptation.dimensionsM) : '尺寸未知'}</dd></div>
              </dl>
              <h3 className="asset-subheading">用途证据</h3>
              <dl className="asset-kv">{(Object.keys(usageLabels) as (keyof typeof usageLabels)[]).map(name => <div key={name}>
                <dt>{usageLabels[name]}</dt><dd>{usageLabel(entry.usage[name].eligible)}<small className="catalog-reason">{entry.usage[name].evidence}</small></dd>
              </div>)}</dl>
              {(entry.adaptation.gaps.length > 0 || entry.adaptation.approximations.length > 0 || entry.unknown.length > 0) && <>
                <h3 className="asset-subheading">缺口与未知项</h3>
                <ul className="catalog-reasons">
                  {entry.adaptation.gaps.map(gap => <li key={gap}>{gap}</li>)}
                  {entry.adaptation.approximations.map(item => <li key={item}>{item}</li>)}
                  {entry.unknown.map(item => <li key={item}>未知属性：{labelFor(item)}</li>)}
                </ul>
              </>}
              <h3 className="asset-subheading">上游验证与获取资格</h3>
              <p className="catalog-reason">{entry.upstreamValidation.profile} {entry.upstreamValidation.profileVersion} · {entry.upstreamValidation.date}</p>
              {entry.upstreamValidation.failingFeatures.length > 0 && <p className="catalog-reason">未通过特性：{entry.upstreamValidation.failingFeatures.join('、')}</p>}
              <p className="catalog-acquisition"><span className={`asset-chip is-${entry.acquisition.eligible ? 'present' : 'missing'}`}><i />{entry.acquisition.eligible ? '可获取' : '不可获取'}</span></p>
              {entry.acquisition.reasons.length > 0 && <ul className="catalog-reasons">{entry.acquisition.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
            </div></section>
            <section className="asset-panel"><header><h2>已安装版本与预览</h2></header><div className="asset-panel-body">
              {entry.installed.versions.length
                ? <ul className="catalog-installed">{entry.installed.versions.map(version => <li key={version.id}>
                  <code>{version.id}</code><small>{version.contentHash}</small>
                  {version.usage && <small>用途：布景 {usageLabel(version.usage.dressing)} · 静态碰撞 {usageLabel(version.usage.staticCollision)}</small>}
                  {version.license && <small>许可：{version.license.unknown ? '未知' : version.license.name} {version.license.unknown ? '' : version.license.admitted ? '已准入' : '未准入'}</small>}
                  {version.unknown && version.unknown.length > 0 && <small>未知：{version.unknown.map(labelFor).join('、')}</small>}
                </li>)}</ul>
                : <p className="asset-small-note">尚未安装本地版本；候选只显示为在线条目，不冒充已安装 Asset。</p>}
              {entry.installed.truncated && <p className="asset-small-note">仅显示最近 20 个本地版本；其余版本见本地素材库。</p>}
              <div className="catalog-detail-preview">{(entry.preview.kind === 'local' || entry.preview.kind === 'cached') && imageUrl(entry.preview.url)
                ? <img src={entry.preview.url!} alt={`${entry.names.zh} 的本地版本预览`} />
                : <div className="catalog-preview-placeholder"><b>预览未缓存</b><small>{entry.preview.reason ?? '获取流程会解析来源预览；页面不会请求远程图片。'}</small></div>}</div>
            </div></section>
          </div>
          <div className="catalog-detail-stack">
            <section className="asset-panel catalog-estimate"><header><h2>有界估算</h2></header><div className="asset-panel-body">
            <dl className="asset-kv">
              <div><dt>来源包</dt><dd>{formatBytes(estimate.source.bytes)} · {estimate.source.files} 个文件</dd></div>
              <div><dt>传输上界</dt><dd>{formatBytes(estimate.transfer.upperBoundBytes)}</dd></div>
              <div><dt>临时空间</dt><dd>{formatBytes(estimate.temporary.upperBoundBytes)}</dd></div>
              <div><dt>适配成品</dt><dd>{estimate.adaptedPackage.known ? formatBytes(estimate.adaptedPackage.bytes) : `未知；上界 ${formatBytes(estimate.adaptedPackage.upperBoundBytes)}`}</dd></div>
              <div><dt>必须磁盘</dt><dd>{formatBytes(estimate.requiredDiskBytes)}</dd></div>
              <div><dt>缓存复用</dt><dd>{estimate.cache.known ? formatBytes(estimate.cache.reusedBytes) : '未知'}<small className="catalog-reason">{estimate.cache.reason}</small></dd></div>
              <div><dt>限额内</dt><dd>{estimate.withinLimits ? '是' : '否'}</dd></div>
              <div><dt>限额</dt><dd>来源 {formatBytes(estimate.limits.sourcePackageBytes)} · 成品 {formatBytes(estimate.limits.adaptedPackageBytes)} · 批量 {formatBytes(estimate.limits.batchBytes)}</dd></div>
              <div><dt>许可决定</dt><dd>{estimate.license.admitted ? '已准入' : '未准入'}{estimate.license.reasons.length ? ` · ${estimate.license.reasons.join('、')}` : ''}</dd></div>
              <div><dt>用途</dt><dd>{(Object.keys(usageLabels) as (keyof typeof usageLabels)[]).map(name => `${usageLabels[name]} ${usageLabel(estimate.usage[name])}`).join(' · ')}</dd></div>
              <div><dt>获取资格</dt><dd><span className={`asset-chip is-${estimate.acquisition.eligible ? 'present' : 'missing'}`}><i />{estimate.acquisition.eligible ? '可获取' : '不可获取'}</span></dd></div>
              <div><dt>网络</dt><dd>{estimate.networkRequired ? '获取时需要网络；估算本身不联网' : '无需网络'}</dd></div>
              <div><dt>主文件</dt><dd>{estimate.downloadsMainFiles ? '会下载主文件' : '估算不下载主文件'}</dd></div>
            </dl>
            {estimate.acquisition.reasons.length > 0 && <ul className="catalog-reasons">{estimate.acquisition.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
            {estimate.unknown.length > 0 && <p className="asset-small-note">未知项：{estimate.unknown.map(labelFor).join('、')}</p>}
            <p className="asset-small-note">估算分别列出传输、临时空间、适配成品与必须磁盘；未知缓存不按零计算。</p>
          </div></section>
            <AcquisitionStart ref={entry.ref} eligible={entry.acquisition.eligible}
              reasons={entry.acquisition.reasons} estimate={estimate} />
          </div>
        </div>
        : <div className="asset-empty" role="alert">目录详情不可用。</div>}
  </section>;
}
