import { useEffect, useRef, useState } from 'react';
import './catalog-sync.css';
import {
  cancelCatalogSync, formatBytes, getCatalogSync, retryCatalogSync, shortRevision,
  startCatalogSync, type CatalogSyncState,
} from '../lib/catalog';

const labels = {
  running: '同步中', cancelling: '正在取消', succeeded: '同步完成',
  failed: '同步失败', cancelled: '已取消', interrupted: '上次同步中断',
};
const stages = { enumerating: '枚举上游全部目录', indexing: '同步概览与缩略图', publishing: '构建检索快照', done: '已结束' };

export function CatalogSyncPanel({ onPublished }: { onPublished: () => void }) {
  const [state, setState] = useState<CatalogSyncState>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const snapshot = useRef<string | null>(null);
  const callback = useRef(onPublished);
  callback.current = onPublished;
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await getCatalogSync(controller.signal);
        if (controller.signal.aborted) return;
        setState(value); setError(undefined);
        if (snapshot.current && value.snapshot?.id !== snapshot.current) callback.current();
        snapshot.current = value.snapshot?.id ?? null;
        timer = setTimeout(poll, value.current?.status === 'running' || value.current?.status === 'cancelling' ? 1500 : 10000);
      } catch (failure) {
        if (!controller.signal.aborted) setError((failure as Error).message);
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [attempt]);
  const record = state?.current;
  const active = record?.status === 'running' || record?.status === 'cancelling';
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true); setError(undefined);
    try { await action(); setAttempt(value => value + 1); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  };
  const metrics = state?.snapshot?.sync;
  return <section className="catalog-sync-panel" aria-label="在线目录同步">
    <div className="catalog-sync-heading"><div><h2>在线目录</h2>
      <p>同步 SimReady 全量概览与上游缩略图；模型文件按需获取。</p></div>
      <div className="catalog-sync-actions">
        {active ? <button disabled={busy || record?.status === 'cancelling'}
          onClick={() => void run(() => cancelCatalogSync(record!.id))}>取消同步</button>
          : <button disabled={busy} onClick={() => void run(startCatalogSync)}>同步全量目录</button>}
        {record && ['failed', 'cancelled', 'interrupted'].includes(record.status) && !active
          && <button disabled={busy} onClick={() => void run(() => retryCatalogSync(record.id))}>重试同步</button>}
      </div>
    </div>
    <p className="asset-small-note">{metrics
      ? `当前目录 ${metrics.indexed} / ${metrics.discovered} 项 · 详情缺口 ${metrics.metadataFailed} · 已缓存预览 ${metrics.previewsCached + metrics.previewsReused} · 无上游预览 ${metrics.previewsMissing} · 预览失败 ${metrics.previewsFailed}`
      : '当前使用精选离线目录，尚未完成全量同步。'}
      {state?.snapshot?.indexedAt && ` · 最近成功索引 ${state.snapshot.indexedAt}`}</p>
    {record && <div className="catalog-sync-progress" aria-live="polite">
      <span className={`asset-chip is-${record.status === 'succeeded' ? 'present' : record.status === 'failed' ? 'missing' : 'warning'}`}><i />{labels[record.status]}</span>
      <span>{stages[record.stage]} · 已索引 {record.indexed} / {record.discovered || '枚举中'} 项</span>
      {record.revision && <code title={record.revision}>{shortRevision(record.revision)}</code>}
      <span>复用概览 {record.metadataReused} 项 · 已缓存图片 {record.previewsCached + record.previewsReused} 项</span>
      <span>响应体传输 {formatBytes(record.networkBytes)} · 缓存 {formatBytes(record.cacheBytes)}</span>
      {record.errors.length > 0 && <details><summary>查看同步缺口（{record.errors.length} 条摘要）</summary>
        <ul>{record.errors.map((item, index) => <li key={index}>{item.package ? `${item.package}：` : ''}{item.message}</li>)}</ul></details>}
    </div>}
    {error && <p role="alert">{error} <button onClick={() => setAttempt(value => value + 1)}>重新读取状态</button></p>}
  </section>;
}
