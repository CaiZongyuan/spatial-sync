import { useEffect, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import {
  filterLabels, formatAge, formatBytes, getCatalogEntry, getCatalogEstimate, semanticLabel,
  usageLabel, usageLabels, zeroReasonLabels,
  type CatalogCandidate, type CatalogEntryDocument, type CatalogEstimateDocument,
  type CatalogSearchResponse, type CatalogSemantic, type CatalogSnapshot,
} from '../lib/catalog';
import { assetKinds, dimensionsLabel } from '../lib/asset-library';
import { evidencePath } from '../lib/catalog-maintenance';
import { request } from '../lib/api';
import { AcquisitionStart } from './AcquisitionPanel';

const attributeLabels: Record<string, string> = {
  dimensionsM: '尺寸', dressing: '布景', staticCollision: '静态碰撞', license: '许可',
  provider: '来源', sourceBytes: '来源字节', usage: '用途', kind: '种类',
};
const labelFor = (id: string) => filterLabels[id] ?? attributeLabels[id] ?? id;
const imageUrl = (url: string | null | undefined) =>
  url && /\.(png|jpe?g|webp)(\?.*)?$/i.test(url) ? url : null;

export function CatalogSnapshotBar({ snapshot, semantic }: {
  snapshot: CatalogSnapshot; semantic?: CatalogSemantic;
}) {
  return <details className="catalog-snapshot" aria-label="目录快照">
    <summary>{snapshot.entryCount} 条 · {snapshot.offlineReadable ? '快照可离线读取' : '快照不可离线读取'} · {formatAge(snapshot.ageSeconds)}</summary>
    <span className="catalog-snapshot-item">快照 <code title={snapshot.id ?? undefined}>{snapshot.id ?? '不可用'}</code></span>
    <span className="catalog-snapshot-item">索引时间 <span>{snapshot.indexedAt ?? '未知'}</span> · {snapshot.coverage ?? '覆盖范围未知'}</span>
    <span className="catalog-snapshot-item">{snapshot.networkRequired ? '检索需要网络' : '检索不联网'}</span>
    {snapshot.metadataRelease?.collections.map(collection => <span className="catalog-snapshot-item" key={collection.id}>
      {collection.id}：清单 {String(collection.coverage.entries ?? '未知')} · 已整理 {String(collection.coverage.organized ?? '未知')}
    </span>)}
    {semantic && <span className="catalog-snapshot-item" title={semantic.notice}>{semanticLabel(semantic)}{semantic.applied ? ` · 本次召回 ${semantic.hits} 条` : ''}</span>}
  </details>;
}

export function CatalogZeroResult({ response, onAction }: {
  response: CatalogSearchResponse;
  onAction: (action: 'remove_filter' | 'broaden_scope' | 'enable_approximation', filter?: string) => void;
}) {
  const zero = response.zeroResult!;
  return <div className="catalog-zero">
    <h2>没有匹配的候选</h2><p>{zeroReasonLabels[zero.reason] ?? zero.reason}</p>
    <ul className="catalog-zero-messages">{zero.messages.map(message => <li key={message}>{message}</li>)}</ul>
    {zero.excludedBy.length > 0 && <ul>{zero.excludedBy.map(item => <li key={item.filter}>{labelFor(item.filter)}：{item.count} 个候选被排除</li>)}</ul>}
    <div className="catalog-zero-actions">{zero.suggestions.map(suggestion => suggestion.action === 'remove_filter'
      ? <button key={`remove:${suggestion.filter}`} onClick={() => onAction('remove_filter', suggestion.filter)}>移除条件：{labelFor(suggestion.filter!)}</button>
      : <button key={suggestion.action} onClick={() => onAction(suggestion.action)}>{suggestion.action === 'broaden_scope' ? '搜索全部范围' : '允许近似结果'}</button>)}
      <button className="catalog-zero-clear" onClick={() => onAction('remove_filter')}>清除筛选</button>
    </div>
  </div>;
}

const previewLabels = {
  not_collected: '预览未采集', available: '物体预览', missing: '来源缺少物体图', failed: '预览读取失败', suspect: '预览待检查',
};
function CandidatePreview({ candidate }: { candidate: CatalogCandidate }) {
  const frame = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [failed, setFailed] = useState(candidate.preview.state === 'failed');
  const [loaded, setLoaded] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    setFailed(candidate.preview.state === 'failed'); setLoaded(false); setAttempt(0);
  }, [candidate.id, candidate.preview.url, candidate.preview.state]);
  useEffect(() => {
    if (!frame.current) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    });
    observer.observe(frame.current);
    return () => observer.disconnect();
  }, [candidate.id, candidate.preview.url]);
  let localUrl = imageUrl(candidate.local?.asset.thumbnailUrl) ?? (candidate.preview.kind === 'on-demand'
    ? candidate.preview.url : imageUrl(candidate.preview.url));
  if (attempt && candidate.preview.url?.includes('/maintenance/thumbnails/')) {
    localUrl = `/api/v1/library/catalog/maintenance/preview?entry=${encodeURIComponent(candidate.id)}${candidate.metadata?.release ? `&release=${encodeURIComponent(candidate.metadata.release)}` : ''}`;
  }
  const state = failed ? 'failed' : candidate.preview.state;
  const reason = state ? previewLabels[state] : '预览未缓存';
  if (['local', 'cached', 'on-demand'].includes(candidate.preview.kind) && localUrl)
    return <div className="catalog-demand-preview" ref={frame} aria-busy={visible && !loaded && !failed}>
      {failed ? <div className="catalog-preview-placeholder"><b>预览读取失败</b><button onClick={() => {
        setFailed(false); setLoaded(false); setAttempt(value => value + 1);
      }}>重试预览</button></div> : visible ? <><img
        src={`${localUrl}${attempt ? `${localUrl.includes('?') ? '&' : '?'}retry=1&attempt=${attempt}` : ''}`}
        className="catalog-cached-preview" alt={`${candidate.title} 的物体预览`}
        onLoad={() => setLoaded(true)} onError={() => setFailed(true)} />
        {!loaded && <span className="catalog-preview-loading" role="status">正在读取预览…</span>}</>
        : <div className="catalog-preview-placeholder"><b>{reason}</b><small>滚动到此处时读取</small></div>}
    </div>;
  return <div className="catalog-preview-placeholder" ref={frame} role="img" aria-label={reason}>
    <b>{reason}</b><small>{candidate.preview.reason ?? '该素材暂未提供物体预览。'}</small>
  </div>;
}

function OnlineCandidateCard({ candidate, index, onDetail }: {
  candidate: CatalogCandidate; index: number; onDetail: () => void;
}) {
  const approximate = candidate.match.unsatisfied.length > 0 || candidate.match.unknown.length > 0;
  const secondaryName = candidate.title === candidate.names.zh ? candidate.names.en : candidate.names.zh;
  return <article className="catalog-card is-online" data-candidate={candidate.id}>
    <div className="catalog-card-figure"><CandidatePreview candidate={candidate} /></div>
    <div className="asset-figure-caption"><b>{candidate.source.provider}</b><span>{candidate.installed.versions.length ? '已在本地' : candidate.license.unknown ? '未审查／上游声明' : candidate.acquisition.eligible ? '可获取' : '在线候选'}</span></div>
    <h2 className="catalog-card-title">{candidate.title}{secondaryName && secondaryName !== candidate.title ? <small>{secondaryName}</small> : null}</h2>
    <p className="catalog-description">{candidate.descriptions.zh || candidate.descriptions.en || '来源尚未提供摘要'}</p>
    <div className="catalog-tags">{candidate.category && <span className="asset-chip">{candidate.category}</span>}
      {candidate.tags?.slice(0, 3).map(tag => <span className="asset-chip" key={tag}>{tag}</span>)}</div>
    {approximate && <div className="catalog-candidate-warnings"><span className="asset-chip is-warning">近似</span>
      <span>{candidate.match.unsatisfied.map(labelFor).join('、')}{candidate.match.unknown.length > 0 && ` · 未知 ${candidate.match.unknown.map(labelFor).join('、')}`}</span>
      <span>{(Object.keys(usageLabels) as (keyof typeof usageLabels)[]).map(name => `${usageLabels[name]} ${usageLabel(candidate.usage[name])}`).join(' · ')}</span>
    </div>}
    <button className="catalog-detail-toggle" onClick={onDetail} aria-label={`查看 ${candidate.title} 详情`}>查看详情</button>
    <span className="catalog-card-order" aria-hidden="true">{index + 1}</span>
  </article>;
}
function LocalCandidateCard({ candidate, index }: { candidate: CatalogCandidate; index: number }) {
  const asset = candidate.local!.asset;
  const kind = asset.kind ?? candidate.kind;
  const name = candidate.local!.id.split('/')[1]?.split('@')[0] ?? '';
  return <article className="catalog-card is-local" data-candidate={candidate.id}>
    <div className="catalog-card-figure"><CandidatePreview candidate={candidate} /></div>
    <div className="asset-figure-caption"><b>本地素材</b><code>{candidate.id}</code></div>
    <h2 className="catalog-card-title">{candidate.title}</h2><p className="catalog-description">{candidate.descriptions.zh || candidate.descriptions.en}</p>
    <p className="asset-small-note">{kind && kind in assetKinds ? assetKinds[kind as keyof typeof assetKinds] : kind} · {dimensionsLabel(asset.dimensionsM)}</p>
    <Link className="catalog-detail-toggle" to="/assets/$kind/$name/$rev" params={{ kind, name, rev: String(candidate.local!.rev) }}>打开本地素材</Link>
    <span className="catalog-card-order" aria-hidden="true">{index + 1}</span>
  </article>;
}

export function CatalogCandidateList({ response, view = 'card', onDetail }: {
  response: CatalogSearchResponse; view?: 'card' | 'list'; onDetail: (candidate: CatalogCandidate) => void;
}) {
  return <div className={`catalog-result-grid${view === 'list' ? ' is-list' : ''}`}>{response.results.map((candidate, index) => candidate.type === 'online'
    ? <OnlineCandidateCard key={`online:${candidate.id}`} candidate={candidate} index={index + (response.pagination?.offset ?? 0)} onDetail={() => onDetail(candidate)} />
    : <LocalCandidateCard key={`local:${candidate.id}`} candidate={candidate} index={index + (response.pagination?.offset ?? 0)} />)}</div>;
}

function EvidenceRecord({ digest }: { digest: string }) {
  const [value, setValue] = useState<unknown>();
  const [error, setError] = useState<string>();
  return <div className="catalog-evidence-record"><button onClick={() => {
    setError(undefined);
    void request<unknown>(evidencePath(digest)).then(setValue).catch((failure: Error) => setError(failure.message));
  }}>读取依据</button> <code>{digest}</code>{error && <p role="alert">{error}</p>}
    {value !== undefined && <pre>{JSON.stringify(value, null, 2)}</pre>}</div>;
}

export function CatalogDetailPanel({ candidate, release, onClose }: {
  candidate: CatalogCandidate; release?: string; onClose: () => void;
}) {
  const [entry, setEntry] = useState<CatalogEntryDocument>();
  const [estimate, setEstimate] = useState<CatalogEstimateDocument>();
  const [error, setError] = useState<string>();
  const [estimateError, setEstimateError] = useState<string>();
  const [preparing, setPreparing] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [proof, setProof] = useState<CatalogEntryDocument>();
  const [proofError, setProofError] = useState<string>();
  const [detailAttempt, setDetailAttempt] = useState(0);
  const { provider, packageId, revision } = candidate.source;
  useEffect(() => {
    const controller = new AbortController();
    setEntry(undefined); setEstimate(undefined); setError(undefined); setPreparing(false); setEstimateError(undefined);
    setEvidenceOpen(false); setProof(undefined); setProofError(undefined);
    if (!provider || !packageId || !revision) { setError('候选缺少固定来源版本，无法读取详情。'); return () => controller.abort(); }
    void getCatalogEntry(provider, packageId, revision, 'summary', controller.signal, release)
      .then(value => { if (!controller.signal.aborted) setEntry(value); })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [candidate.id, provider, packageId, revision, release, detailAttempt]);
  useEffect(() => {
    if (!evidenceOpen || !entry || proof) return;
    const controller = new AbortController();
    void getCatalogEntry(provider!, packageId!, revision!, 'full', controller.signal, release, entry.documentDigest)
      .then(value => { if (!controller.signal.aborted) setProof(value); })
      .catch((failure: Error) => { if (!controller.signal.aborted) setProofError(failure.message); });
    return () => controller.abort();
  }, [evidenceOpen, entry, proof, provider, packageId, revision, release]);
  useEffect(() => {
    if (!preparing || !provider || !packageId || !revision) return;
    const controller = new AbortController();
    void getCatalogEstimate(provider, packageId, revision, controller.signal, release)
      .then(value => { if (!controller.signal.aborted) setEstimate(value); })
      .catch((failure: Error) => { if (!controller.signal.aborted) setEstimateError(failure.message); });
    return () => controller.abort();
  }, [preparing, provider, packageId, revision, release]);
  return <section className="catalog-detail" aria-label="候选详情">
    <header className="catalog-detail-heading"><div><h2>{candidate.names.zh ?? candidate.title}</h2><p>{candidate.names.en}</p></div><button onClick={onClose}>关闭详情</button></header>
    {error ? <p role="alert">{error}</p> : !entry ? <p role="status">正在读取目录详情…</p> : <div className="catalog-detail-grid">
      <div className="catalog-detail-stack">
        <section className="asset-panel"><header><h2>概览</h2></header><div className="asset-panel-body">
          <p>{entry.descriptions.zh || entry.descriptions.en}</p>
          <div className="catalog-tags">{entry.metadata?.category && <span className="asset-chip">{entry.metadata.category}</span>}{entry.metadata?.tags?.map(tag => <span className="asset-chip" key={tag}>{tag}</span>)}</div>
          <dl className="asset-kv"><div><dt>来源</dt><dd>{entry.provider}<a className="catalog-source-link" href={entry.source.url} target="_blank" rel="noreferrer">查看来源说明</a></dd></div>
            <div><dt>许可声明</dt><dd>{entry.license.name ?? '未知'} · {entry.license.unknown ? '未审查／上游声明' : entry.license.admitted ? '已准入' : '未准入'}</dd></div>
            <div><dt>本地版本</dt><dd>{entry.installed.versions.length ? entry.installed.versions.map(value => <code key={value.id}>{value.id}</code>) : '尚未安装本地版本'}</dd></div>
            <div><dt>获取状态</dt><dd>{entry.acquisition.eligible ? '可获取' : '不可获取'}{entry.acquisition.reasons.map(reason => <small className="catalog-reason" key={reason}>{reason}</small>)}</dd></div>
          </dl>
          {entry.metadata?.organization && <p className="asset-small-note">整理：{entry.metadata.organization.status === 'organized' ? '已整理' : '待整理'} · 版本 {entry.metadata.organization.version}</p>}
        </div></section>
        <section className="asset-panel"><header><h2>技术信息</h2></header><div className="asset-panel-body"><dl className="asset-kv">
          <div><dt>来源文件</dt><dd>{formatBytes(entry.source.bytes)} · {entry.source.files ?? '未知'} 个文件</dd></div>
          <div><dt>尺寸</dt><dd>{dimensionsLabel(entry.adaptation.dimensionsM)}</dd></div>
          <div><dt>适配</dt><dd>{entry.adaptation.status}</dd></div>
          {(Object.keys(usageLabels) as (keyof typeof usageLabels)[]).map(name => <div key={name}><dt>{usageLabels[name]}</dt><dd>{usageLabel(entry.usage[name].eligible)}<small className="catalog-reason">{entry.usage[name].evidence}</small></dd></div>)}
          {entry.metadata?.facts && Object.entries(entry.metadata.facts).filter(([name]) => !['sourceBytes', 'fileCount', 'dimensionsM', 'license'].includes(name)).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value === null ? '未知' : typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}
        </dl></div></section>
        <details className="asset-panel catalog-evidence" open={evidenceOpen}
          onToggle={event => setEvidenceOpen(event.currentTarget.open)}><summary>完整身份、未知项与来源依据</summary><div className="asset-panel-body">
          {evidenceOpen && !proof && !proofError && <p role="status">正在读取完整依据…</p>}
          {proofError && <p role="alert">{proofError} <button onClick={() => setDetailAttempt(value => value + 1)}>重新读取详情</button></p>}
          <dl className="asset-kv"><div><dt>精确引用</dt><dd><code>{entry.ref}</code></dd></div><div><dt>固定来源版本</dt><dd><code>{entry.revision}</code></dd></div>
            <div><dt>元数据发布</dt><dd><code>{entry.metadata?.release ?? release ?? '精选目录'}</code></dd></div>
            <div><dt>许可证据</dt><dd>{entry.license.evidence.file ?? '尚未审查；声明不等于获取准入'}<small className="catalog-hash">{entry.license.evidence.sha256}</small></dd></div>
          </dl>
          {(proof ?? entry).license.attachments.map(value => <p key={value.file}><code>{value.file}</code> · {value.license}</p>)}
          <ul className="catalog-reasons">{[...(proof ?? entry).adaptation.gaps, ...(proof ?? entry).adaptation.approximations].map(value => <li key={value}>{value}</li>)}{(proof ?? entry).unknown.map(value => <li key={value}>未知属性：{labelFor(value)}</li>)}</ul>
          {(proof ?? entry).metadata?.evidence?.map((value, index) => <div key={value.id ?? index}><p>{value.kind} · {value.origin ?? value.path} · {value.fields?.join('、')}</p>{(value.sha256 ?? value.id)?.startsWith('sha256:') && <EvidenceRecord digest={(value.sha256 ?? value.id)!} />}</div>)}
          {candidate.match.reasons.length > 0 && <p className="catalog-match">匹配：{candidate.match.reasons.map(value => `${value.field}「${value.value}」`).join('，')}</p>}
          {entry.previewTruncated && <p>长依据按响应预算裁剪；不表示来源没有更多条目。</p>}
        </div></details>
      </div>
      <div className="catalog-detail-stack">
        <div className="catalog-detail-preview"><CandidatePreview candidate={{ ...candidate, preview: entry.preview }} /></div>
        {!preparing ? <button className="catalog-detail-toggle" onClick={() => setPreparing(true)}>准备获取 · 查看预算</button>
          : estimateError ? <p role="alert">{estimateError}</p> : !estimate ? <p role="status">正在估算获取预算…</p> : <>
            <section className="asset-panel catalog-estimate"><header><h2>获取预算</h2></header><div className="asset-panel-body"><dl className="asset-kv">
              <div><dt>来源包</dt><dd>{formatBytes(estimate.source.bytes)} · {estimate.source.files ?? '未知'} 个文件</dd></div>
              <div><dt>传输上界</dt><dd>{formatBytes(estimate.transfer.upperBoundBytes)}</dd></div>
              <div><dt>临时空间</dt><dd>{formatBytes(estimate.temporary.upperBoundBytes)}</dd></div>
              <div><dt>适配成品</dt><dd>{estimate.adaptedPackage.known ? formatBytes(estimate.adaptedPackage.bytes) : `未知；上界 ${formatBytes(estimate.adaptedPackage.upperBoundBytes)}`}</dd></div>
              <div><dt>必须磁盘</dt><dd>{formatBytes(estimate.requiredDiskBytes)}</dd></div>
              <div><dt>缓存复用</dt><dd>{estimate.cache.known ? formatBytes(estimate.cache.reusedBytes) : '未知'}<small className="catalog-reason">{estimate.cache.reason}</small></dd></div>
              <div><dt>限额</dt><dd>来源 {formatBytes(estimate.limits.sourcePackageBytes)} · 批量 {formatBytes(estimate.limits.batchBytes)}</dd></div>
              <div><dt>许可决定</dt><dd>{estimate.license.admitted ? '已准入' : '未准入'}</dd></div>
            </dl><ul className="catalog-reasons">{estimate.acquisition.reasons.map(value => <li key={value}>{value}</li>)}</ul><p className="asset-small-note">估算不下载主文件。未知缓存与大小保持未知。</p></div></section>
            <AcquisitionStart ref={entry.ref} eligible={entry.acquisition.eligible} reasons={entry.acquisition.reasons} estimate={estimate} />
          </>}
      </div>
    </div>}
  </section>;
}
