import { useCallback, useEffect, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { formatBytes, type CatalogEstimateDocument } from '../lib/catalog';
import {
  acquisitionErrorLabels, acquisitionErrorMessage, acquisitionPhaseLabels, acquisitionStatusLabels,
  cancelAcquisition, getAcquisition, isActiveAcquisition, listAcquisitions, localAssetTarget,
  retryAcquisition, startAcquisition,
  type AcquisitionRecord, type AcquisitionStatus,
} from '../lib/acquisition';

const statusTone = (status: AcquisitionStatus) =>
  status === 'succeeded' ? 'present'
    : status === 'failed' ? 'missing'
      : status === 'cancelled' ? 'not_applicable'
        : 'warning';

function usageLine(record: AcquisitionRecord) {
  return `来源 ${formatBytes(record.usage.sourceBytes)} · 传输 ${formatBytes(record.usage.transferBytes)} · `
    + `复用 ${formatBytes(record.usage.cacheReusedBytes)} · 适配 ${formatBytes(record.usage.adaptedBytes)} · `
    + `临时 ${formatBytes(record.usage.temporaryBytes)}`;
}

export function AcquisitionRecordPanel({ record, busy = false, onCancel, onRetry }: {
  record: AcquisitionRecord;
  busy?: boolean;
  onCancel?: () => void;
  onRetry?: () => void;
}) {
  const target = localAssetTarget(record);
  const active = isActiveAcquisition(record);
  const percent = record.progress.totalBytes > 0
    ? Math.min(100, Math.round((record.progress.completedBytes / record.progress.totalBytes) * 100))
    : 0;
  return <article className="acquisition-record" data-acquisition-id={record.id}
    data-acquisition-status={record.status}>
    <header className="acquisition-record-head">
      <div><code>{record.id}</code><small>{record.catalogRef}</small></div>
      <span className={`asset-chip is-${statusTone(record.status)}`}><i />{acquisitionStatusLabels[record.status]}</span>
    </header>
    <dl className="asset-kv acquisition-facts">
      <div><dt>尝试</dt><dd>第 {record.attempt} 次{record.cancelRequested ? ' · 已请求取消' : ''}</dd></div>
      <div><dt>阶段</dt><dd>{acquisitionPhaseLabels[record.progress.phase]}
        {record.progress.message ? <small className="catalog-reason">{record.progress.message}</small> : null}</dd></div>
      <div><dt>字节进度</dt><dd>{formatBytes(record.progress.completedBytes)} / {formatBytes(record.progress.totalBytes)} · {percent}%</dd></div>
      <div><dt>文件进度</dt><dd>{record.progress.completedFiles} / {record.progress.totalFiles}</dd></div>
      <div><dt>实际用量</dt><dd>{usageLine(record)}</dd></div>
      <div><dt>预检限额</dt><dd>必须磁盘 {formatBytes(record.estimate.requiredDiskBytes)} · <span className={`asset-chip is-${record.estimate.withinLimits ? 'present' : 'missing'}`}><i />{record.estimate.withinLimits ? '限额内' : '超出限额'}</span>
        <small className="catalog-reason">来源 {formatBytes(record.estimate.limits.sourcePackageBytes)} · 成品 {formatBytes(record.estimate.limits.adaptedPackageBytes)} · 批量 {formatBytes(record.estimate.limits.batchBytes)} · 缓存 {formatBytes(record.estimate.limits.rawCacheBytes)}</small></dd></div>
    </dl>
    {active && <progress className="acquisition-progress" aria-label="获取字节进度" max={Math.max(record.progress.totalBytes, 1)}
      value={record.progress.completedBytes} />}
    {record.result ? <div className="acquisition-result">
      <h3 className="asset-subheading">已发布的本地版本</h3>
      <dl className="asset-kv">
        <div><dt>素材版本</dt><dd>{target
          ? <Link to="/assets/$kind/$name/$rev" params={target}>{record.result.assetVersion}</Link>
          : record.result.assetVersion}</dd></div>
        <div><dt>内容哈希</dt><dd><code title={record.result.contentHash}>{record.result.contentHash.slice(0, 16)}…</code></dd></div>
        <div><dt>校验</dt><dd>{record.result.validation.status === 'launchable' ? '可启动' : '存在缺口'}
          {record.result.validation.gapCount > 0 ? ` · ${record.result.validation.gapCount} 项缺口` : ''}</dd></div>
        <div><dt>去重</dt><dd>{record.result.deduplicated ? '复用已有相同内容版本' : '新发布版本'}</dd></div>
        <div><dt>记录用途</dt><dd>布景 {record.result.usage.dressing === true ? '是' : record.result.usage.dressing === false ? '否' : '未知'}
          {' · '}静态碰撞 {record.result.usage.staticCollision === true ? '是' : record.result.usage.staticCollision === false ? '否' : '未知'}</dd></div>
      </dl>
      <p className="asset-small-note">这是获取记录的结果；导入与用途仍是分别记录的事实。</p>
    </div> : null}
    {record.error ? <div className="acquisition-failure" role="alert">
      <p><b>{acquisitionErrorLabels[record.error.code] ?? record.error.code}</b> · {record.error.message}</p>
      <p className="asset-small-note">{record.error.retryable ? '可以重试；已发布版本不受影响。' : '不可重试；请先处理原因。'}</p>
    </div> : null}
    {(active || record.status === 'failed' || record.status === 'cancelled' || target) && <div className="acquisition-actions">
      {active && onCancel && <button disabled={busy} onClick={onCancel}>取消获取</button>}
      {(record.status === 'cancelled'
        || (record.status === 'failed' && record.error?.retryable !== false)) && onRetry
        && <button disabled={busy} onClick={onRetry}>重试获取</button>}
      {target && <Link className="catalog-detail-toggle" to="/assets/$kind/$name/$rev" params={target}>打开本地素材</Link>}
    </div>}
  </article>;
}

export function AcquisitionStart({ ref: catalogRef, eligible, reasons, estimate }: {
  ref: string;
  eligible: boolean;
  reasons: string[];
  estimate: CatalogEstimateDocument;
}) {
  const [record, setRecord] = useState<AcquisitionRecord>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const active = record !== undefined && isActiveAcquisition(record);
  const identifier = record?.id;

  useEffect(() => {
    if (!identifier || !active) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void getAcquisition(identifier, controller.signal)
        .then(next => { if (!controller.signal.aborted) setRecord(next); })
        .catch(() => undefined);
    }, 800);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [identifier, active]);

  const start = async () => {
    setBusy(true); setError(undefined);
    try {
      setRecord(await startAcquisition(catalogRef));
    } catch (failure) {
      setError(acquisitionErrorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    if (!record) return;
    setBusy(true);
    try { setRecord(await cancelAcquisition(record.id)); }
    catch (failure) { setError(acquisitionErrorMessage(failure)); }
    finally { setBusy(false); }
  };
  const retry = async () => {
    if (!record) return;
    setBusy(true);
    try { setRecord(await retryAcquisition(record.id)); }
    catch (failure) { setError(acquisitionErrorMessage(failure)); }
    finally { setBusy(false); }
  };

  return <section className="asset-panel acquisition-start" aria-label="获取在线候选">
    <header><h2>获取（Acquisition）</h2>
      <span className={`asset-chip is-${eligible ? 'present' : 'missing'}`}><i />{eligible ? '可获取' : '不可获取'}</span></header>
    <div className="asset-panel-body">
      <dl className="asset-kv">
        <div><dt>预检来源</dt><dd>{formatBytes(estimate.source.bytes)} · {estimate.source.files} 个文件</dd></div>
        <div><dt>预检磁盘</dt><dd>{formatBytes(estimate.requiredDiskBytes)} · <span className={`asset-chip is-${estimate.withinLimits ? 'present' : 'missing'}`}><i />{estimate.withinLimits ? '限额内' : '超出限额'}</span></dd></div>
        <div><dt>限额</dt><dd>来源 {formatBytes(estimate.limits.sourcePackageBytes)} · 成品 {formatBytes(estimate.limits.adaptedPackageBytes)} · 批量 {formatBytes(estimate.limits.batchBytes)} · 缓存 {formatBytes(estimate.limits.rawCacheBytes)}</dd></div>
      </dl>
      <p className="asset-small-note">开始获取时按实时缓存与磁盘重新预检；获取记录与导入、用途是不同事实。</p>
      {reasons.length > 0 && <ul className="catalog-reasons">{reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
      {error && <div className="asset-empty acquisition-error" role="alert">{error}</div>}
      {eligible && <div className="acquisition-actions">
        <button disabled={busy} onClick={() => void start()}>{record ? '再次获取' : '开始获取'}</button>
      </div>}
      {record && <AcquisitionRecordPanel record={record} busy={busy} onCancel={() => void cancel()}
        onRetry={() => void retry()} />}
    </div>
  </section>;
}

export function AcquisitionRecords() {
  const [records, setRecords] = useState<AcquisitionRecord[]>();
  const [error, setError] = useState<string>();
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState<string>();

  useEffect(() => {
    const controller = new AbortController();
    setError(undefined);
    void listAcquisitions(undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted) setRecords(value.acquisitions); })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [refresh]);

  const active = records?.some(isActiveAcquisition) ?? false;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setRefresh(value => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  const act = useCallback(async (id: string, operation: (value: string) => Promise<AcquisitionRecord>) => {
    setBusy(id);
    try { await operation(id); setRefresh(value => value + 1); }
    catch (failure) { setError(acquisitionErrorMessage(failure)); }
    finally { setBusy(undefined); }
  }, []);

  return <section className="asset-panel acquisition-records" aria-label="获取记录">
    <header><h2>获取记录</h2>
      <button onClick={() => setRefresh(value => value + 1)}>刷新获取记录</button></header>
    <div className="asset-panel-body">
      {error && <div className="asset-empty acquisition-error" role="alert">{error}</div>}
      {records === undefined ? <p className="asset-empty" role="status">正在读取获取记录…</p>
        : records.length === 0 ? <p className="asset-small-note">尚无获取记录；在线候选详情中的“开始获取”会创建真实记录。</p>
          : <div className="acquisition-record-list">{records.map(record => <AcquisitionRecordPanel
            key={record.id} record={record} busy={busy === record.id}
            onCancel={() => void act(record.id, cancelAcquisition)}
            onRetry={() => void act(record.id, retryAcquisition)} />)}</div>}
    </div>
  </section>;
}
