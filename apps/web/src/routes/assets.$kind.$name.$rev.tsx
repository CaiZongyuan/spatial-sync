import { createFileRoute, Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { assetKinds, dimensionsLabel, getAssetDetail, type AssetDetail } from '../lib/asset-library';
import { AssetCompleteness } from '../components/AssetCompleteness';
import { AssetModelViewport } from '../components/AssetModelViewport';
import { AssetBindings, AssetEntityTypes, AssetProvenance, AssetReferencingScenes, licenseFile } from '../components/AssetDetailPanels';
import { LibraryDeletionRequest } from '../components/LibraryDeletion';

export const Route = createFileRoute('/assets/$kind/$name/$rev')({ component: AssetDetails });
function AssetImage({ url, label }: { url: string; label: string }) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  return failed ? <div className="asset-preview-status" role="alert"><p>预览图无法载入。</p><button onClick={() => { setFailed(false); setAttempt(value => value + 1); }}>重试预览</button></div>
    : <img key={attempt} src={url} alt={label} onError={() => setFailed(true)} />;
}
function AssetDetails() {
  const { kind, name, rev } = Route.useParams();
  const [asset, setAsset] = useState<AssetDetail>();
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setAsset(undefined); setError(undefined);
    void getAssetDetail(kind, name, rev, controller.signal).then(value => { if (!controller.signal.aborted) setAsset(value); })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [kind, name, rev, attempt]);
  const license = asset && licenseFile(asset);
  return <main className="asset-page asset-detail-page">{error ? <div className="asset-empty" role="alert">{error}<button onClick={() => setAttempt(attempt + 1)}>重试</button><Link to="/assets">返回素材库</Link></div> : !asset ? <div className="asset-empty" role="status">正在读取素材版本…</div> : <>
    <header className="asset-page-heading"><h1>{asset.title}</h1><p><code>{asset.id}</code><span>·</span>{assetKinds[asset.kind]}<span>·</span>{dimensionsLabel(asset.dimensionsM)}</p></header>
    <div className="asset-detail-grid"><div className="asset-detail-stack">
      <section className="asset-panel"><header><h2>预览</h2></header><figure className="asset-detail-figure"><div className="asset-detail-model">{asset.visualUrl ? <AssetModelViewport key={asset.contentHash} url={asset.visualUrl} /> : asset.thumbnailUrl ? <AssetImage url={asset.thumbnailUrl} label={`${asset.id} 的素材预览`} /> : <div className="asset-empty">此素材没有视觉文件</div>}</div><figcaption className="asset-figure-caption"><b>图 1</b><code>{asset.id}</code></figcaption></figure></section>
      <section className="asset-panel"><header><h2>完整度清单</h2><span className="asset-small-note">{asset.gapCount} 缺口 / 警告</span></header><div className="asset-panel-body"><AssetCompleteness asset={asset} /><p className="asset-small-note">{asset.validationCoverage}</p></div></section>
      <AssetBindings asset={asset} />
    </div><aside className="asset-detail-stack"><section className="asset-panel"><header><h2>属性</h2></header><dl className="asset-kv asset-panel-body">
      <div><dt>素材 ID</dt><dd><code>{asset.id}</code></dd></div><div><dt>种类</dt><dd>{assetKinds[asset.kind]}</dd></div><div><dt>尺寸</dt><dd>{dimensionsLabel(asset.dimensionsM)}</dd></div><div><dt>实现类型</dt><dd><code>{asset.implementedTypes.join(' · ') || '无'}</code></dd></div><div><dt>来源库</dt><dd>{asset.root === 'builtin' ? '内置库' : '用户库'}</dd></div><div><dt>许可证</dt><dd>{license ? <a href={asset.files[license]} target="_blank" rel="noreferrer">查看原文 ↗</a> : '未提供'}</dd></div><div><dt>不可变版本</dt><dd>是 · {asset.rev}</dd></div>
    </dl></section><AssetEntityTypes asset={asset} /><AssetProvenance asset={asset} /><AssetReferencingScenes asset={asset} /><LibraryDeletionRequest resource="asset" id={asset.id} root={asset.root} /><Link to="/assets" className="asset-back">← 素材库</Link></aside></div>
  </>}</main>;
}
