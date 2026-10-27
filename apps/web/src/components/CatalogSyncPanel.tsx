import { useEffect, useRef, useState } from 'react';
import './catalog-sync.css';
import {
  cancelCatalogSync, getCatalogSync, retryCatalogSync,
  startCatalogSync, type CatalogSyncRecord, type CatalogSyncState,
} from '../lib/catalog';

const labels = {
  running: '目录更新中，可继续搜索', cancelling: '正在取消', succeeded: '目录已更新',
  failed: '更新失败，保留现有目录', cancelled: '已取消', interrupted: '上次更新中断',
};

export function CatalogSyncPanel({ onPublished, provider, onSources }: {
  onPublished: () => void; provider?: string;
  onSources: (sources: { provider: string; dataset: string; entryCount: number }[]) => void;
}) {
  const [state, setState] = useState<CatalogSyncState>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const snapshot = useRef<string | null>(null);
  const callback = useRef(onPublished);
  const sourcesCallback = useRef(onSources);
  callback.current = onPublished;
  sourcesCallback.current = onSources;
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await getCatalogSync(controller.signal);
        if (controller.signal.aborted) return;
        setState(value); setError(undefined);
        sourcesCallback.current(value.sources ?? []);
        if (snapshot.current && value.snapshot?.id !== snapshot.current) callback.current();
        snapshot.current = value.snapshot?.id ?? null;
        timer = setTimeout(poll, value.current?.status === 'running' || value.current?.status === 'cancelling' ? 1500 : 10000);
      } catch (failure) {
        if (!controller.signal.aborted) { setError((failure as Error).message); timer = setTimeout(poll, 10000); }
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [attempt]);
  const record = state?.current;
  const active = record?.status === 'running' || record?.status === 'cancelling';
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true); setError(undefined);
    try {
      const value = await action() as CatalogSyncRecord;
      setState(previous => previous ? { ...previous, current: value } : previous);
      setAttempt(value => value + 1);
    }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  };
  return <section className="catalog-sync-panel" aria-label="在线目录同步">
    <div className="catalog-sync-heading"><div>
      <span>{state?.snapshot ? `来源同步快照 · ${state.snapshot.entryCount} 条` : '来源目录更新'}</span>
      <small>预览与详情按需加载</small></div>
      <div className="catalog-sync-actions">
        {active ? <button disabled={busy || record?.status === 'cancelling'}
          onClick={() => void run(() => cancelCatalogSync(record!.id))}>取消更新</button>
          : <button disabled={busy} onClick={() => void run(() => startCatalogSync(provider))}>{provider ? `更新 ${provider} 目录` : '更新目录'}</button>}
        {record && ['failed', 'cancelled', 'interrupted'].includes(record.status) && !active
          && <button disabled={busy} onClick={() => void run(() => retryCatalogSync(record.id))}>重试更新</button>}
      </div>
    </div>
    {record && <div className="catalog-sync-progress" aria-live="polite">
      <span className={`asset-chip is-${record.status === 'succeeded' ? 'present' : record.status === 'failed' ? 'missing' : 'warning'}`}><i />{labels[record.status]}</span>
      {record.status === 'succeeded' && record.errors.length > 0 && <span>部分来源更新失败，继续使用这些来源的现有目录。</span>}
      {record.message && <span>{record.message}</span>}
      {record.errors.length > 0 && <details><summary>查看来源问题</summary>
        <ul>{record.errors.map((item, index) => <li key={index}>{item.package ? `${item.package}：` : ''}{item.message}</li>)}</ul></details>}
    </div>}
    {error && <p role="alert">{error} <button onClick={() => setAttempt(value => value + 1)}>重新读取状态</button></p>}
  </section>;
}
