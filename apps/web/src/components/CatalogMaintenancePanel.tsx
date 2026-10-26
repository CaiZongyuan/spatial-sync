import { useEffect, useRef, useState } from 'react';
import {
  controlMetadataRun, getMetadataState, setMetadataCollectionEnabled, startMetadataRun,
  type MetadataState,
} from '../lib/catalog-maintenance';
import { formatBytes } from '../lib/catalog';

const activeStatuses = new Set(['queued', 'running', 'cancelling']);
const statusLabels: Record<string, string> = {
  queued: '等待维护', running: '维护中', cancelling: '正在取消', succeeded: '已完成',
  partial: '部分完成', failed: '失败', cancelled: '已取消', interrupted: '已中断',
  pending_runtime: '待配置运行时', budget_exhausted: '预算已用尽',
};
const stageLabels: Record<string, string> = {
  enumerating: '枚举清单', extracting: '提取字段', organizing: '整理元数据',
  checking: '检查', publishing: '发布', finished: '完成',
};

export function CatalogMaintenancePanel({ onPublication, onCollections }: {
  onPublication: () => void;
  onCollections: (values: MetadataState['collections']) => void;
}) {
  const [state, setState] = useState<MetadataState>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const callbacks = useRef({ onPublication, onCollections });
  callbacks.current = { onPublication, onCollections };
  const previousRelease = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await getMetadataState(controller.signal);
        if (controller.signal.aborted) return;
        setState(value); setError(undefined);
        callbacks.current.onCollections(value.collections);
        const releaseId = value.release?.id ?? null;
        if (previousRelease.current !== undefined && previousRelease.current !== releaseId)
          callbacks.current.onPublication();
        previousRelease.current = releaseId;
        timer = setTimeout(poll, value.runs.some(run => activeStatuses.has(run.status)) ? 1500 : 10000);
      } catch (failure) {
        if (!controller.signal.aborted) { setError((failure as Error).message); timer = setTimeout(poll, 10000); }
      }
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [attempt]);
  const act = async (action: () => Promise<unknown>) => {
    setBusy(true); setError(undefined);
    try { await action(); setAttempt(value => value + 1); }
    catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  };
  return <details className="catalog-maintenance" aria-label="元数据维护">
    <summary>元数据维护 <span>{state?.collections.length ?? '…'} 个集合</span></summary>
    <p className="asset-small-note">清单覆盖与整理覆盖分别统计。维护期间仍可浏览当前发布。</p>
    {state && <p className="asset-small-note">{state.runtime.configured
      ? `Agent Runtime：${state.runtime.profile ?? '已配置'}`
      : 'Agent Runtime 待配置；可提取来源已有字段，自动摘要与分类尚待配置。'}</p>}
    {state?.collections.map(collection => {
      const run = state.runs.find(value => value.collectionId === collection.id);
      const coverage = state.release?.collections.find(value => value.id === collection.id)?.coverage ?? {};
      const inventory = coverage.entries ?? coverage.inventory ?? coverage.discovered;
      const organized = coverage.organized;
      const active = run && activeStatuses.has(run.status);
      return <article className="catalog-maintenance-collection" key={collection.id}>
        <div><strong>{collection.id}</strong><small>{collection.dataset}</small></div>
        <p>清单 {typeof inventory === 'number' ? inventory : '未知'} · 已整理 {typeof organized === 'number' ? organized : '未知'}
          {coverage.inventoryComplete === true && ' · 清单完整'}</p>
        <div className="catalog-maintenance-actions">
          <label><input type="checkbox" checked={collection.enabled} disabled={busy}
            onChange={event => void act(() => setMetadataCollectionEnabled(collection.id, event.target.checked))} />启用自动维护</label>
          <button disabled={busy || !!active} onClick={() => void act(() => startMetadataRun(collection.id))}>维护 {collection.id}</button>
          {active && <button disabled={busy || run.status === 'cancelling'}
            onClick={() => void act(() => controlMetadataRun(run.id, 'cancel'))}>取消维护</button>}
          {run && ['failed', 'cancelled', 'interrupted', 'partial', 'budget_exhausted'].includes(run.status) && <>
            <button disabled={busy} onClick={() => void act(() => controlMetadataRun(run.id, 'resume'))}>继续维护</button>
            <button disabled={busy} onClick={() => void act(() => controlMetadataRun(run.id, 'retry'))}>重试失败项</button>
          </>}
        </div>
        {run && <p role="status">{statusLabels[run.status] ?? run.status} · {stageLabels[run.phase] ?? run.phase}
          {run.coverage && ` · 已索引 ${run.coverage.indexed} · 已整理 ${run.coverage.organized} · 失败 ${run.coverage.failed}`}
          {run.usage && ` · 网络 ${formatBytes(run.usage.networkBytes)}`}
          {run.message && ` · ${run.message}`}</p>}
        {run?.errors && run.errors.length > 0 && <details><summary>维护问题</summary><ul>{run.errors.map((value, index) =>
          <li key={index}>{value.entry && `${value.entry}：`}{value.message}</li>)}</ul></details>}
      </article>;
    })}
    {error && <p role="alert">{error} <button onClick={() => setAttempt(value => value + 1)}>重新读取维护状态</button></p>}
  </details>;
}
