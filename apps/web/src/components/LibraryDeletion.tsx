import { Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { ApiError } from '../lib/api';
import { requestLibraryDeletion, type DeletionDetails, type DeletionExecution, type ReferenceInspection } from '../lib/library-deletions';
import { getLibraryRemoval, removalPending, restoreLibraryVersion, type LibraryRemoval } from '../lib/library-deletions';
import '../library-deletions.css';

const executionText: Record<DeletionExecution['status'], string> = {
  committed: '版本已删除，索引已更新；执行后不能撤销。',
  in_progress: '删除尚未提交；若执行中断，重启会恢复此版本。',
  failed: '执行未完成，版本已保留。',
  blocked: '执行检查未通过，版本已保留。',
  not_started: '批准时会重新检查引用与包字节。',
};

export function LibraryReferences({ inspection }: { inspection: Partial<ReferenceInspection> }) {
  return <div className="library-reference-inspection">
    {inspection.references?.length ? <><p>以下对象仍引用此版本：</p><ul>{inspection.references.map((reference, index) => <li key={index}><code>{reference.id}</code> · {reference.kind}<small>{reference.reason}</small></li>)}</ul></> : inspection.complete ? <p>已检查，无引用。</p> : null}
    {inspection.uninspected?.length ? <><p>引用检查未完成，暂不能删除：</p><ul>{inspection.uninspected.map((reference, index) => <li key={index}><code>{reference.id}</code><small>{reference.reason}</small></li>)}</ul></> : null}
  </div>;
}

export function useRemovalProgress(record: LibraryRemoval | undefined, update: (value: LibraryRemoval) => void) {
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (!record || !removalPending(record)) return;
    let active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5500);
    const timer = setTimeout(() => {
      void getLibraryRemoval(record.id, controller.signal).then(value => {
        if (active) { setError(undefined); update(value); }
      }).catch(error => { if (active) setError(controller.signal.aborted ? '读取执行状态超时，请刷新。' : (error as Error).message); });
    }, 500);
    return () => { active = false; clearTimeout(timer); clearTimeout(timeout); controller.abort(); };
  }, [record, update]);
  return error;
}

export function RemovalResult({ record, update }: { record: LibraryRemoval; update: (value: LibraryRemoval) => void }) {
  const [error, setError] = useState<string>(), [busy, setBusy] = useState(false);
  const progressError = useRemovalProgress(record, update);
  const labels: Record<LibraryRemoval['status'], string> = {
    queued: '请求已接收，正在等待检查。', checking: '正在检查引用，尚未删除。',
    committed: '版本已移到回收站，可恢复。', blocked: '检查未通过，版本已保留。',
    failed: '执行未完成，版本已保留。', restoring: '正在恢复原始版本。', restored: '原始版本已恢复。',
  };
  return <div><p role="status">{labels[record.status]}</p>
    {record.status === 'committed' && <><p>回收站保留 {record.retainedBytes.toLocaleString()} 字节，尚未释放这部分磁盘。</p>
      <button disabled={busy} onClick={async () => {
        setBusy(true); setError(undefined);
        try { update(await restoreLibraryVersion(record.id)); } catch (error) { setError((error as Error).message); }
        finally { setBusy(false); }
      }}>恢复此版本</button></>}
    {record.error && <><p role="alert">{record.error.message}</p><LibraryReferences inspection={record.error} /></>}
    {(error || progressError) && <p role="alert">{error || progressError} <button onClick={() => {
      void getLibraryRemoval(record.id).then(update).catch(error => setError((error as Error).message));
    }}>刷新执行状态</button></p>}
  </div>;
}

export function LibraryDeletionRequest({ resource, id, root, contentHash }: {
  resource: 'asset' | 'scene'; id: string; root: 'builtin' | 'user'; contentHash: string;
}) {
  const [busy, setBusy] = useState(false);
  const [record, setRecord] = useState<LibraryRemoval>();
  const [requestId] = useState(() => crypto.randomUUID());
  const [error, setError] = useState<string>();
  const [inspection, setInspection] = useState<Partial<ReferenceInspection>>();
  return <section className="library-deletion-panel" aria-label="删除库版本"><header><h2>删除版本</h2></header><div>
    <p>移出库列表并保留原始包，可从<Link to="/trash">回收站</Link>恢复。</p>
    {root === 'builtin' ? <p>内置库只读，随平台版本发布。</p> : record ? <RemovalResult record={record} update={setRecord} /> : <button disabled={busy} onClick={async () => {
      setBusy(true); setError(undefined); setInspection(undefined);
      try { setRecord(await requestLibraryDeletion(resource, id, contentHash, requestId)); }
      catch (failure) {
        setError((failure as Error).message);
        if (failure instanceof ApiError && failure.detail && typeof failure.detail === 'object') {
          const detail = failure.detail as Partial<ReferenceInspection> & { code?: string };
          setInspection(detail);
          if (detail.code === 'version_in_use') setError('此版本仍被引用，无法移到回收站。');
          if (detail.code === 'reference_inspection_incomplete') setError('引用检查未完成，版本会保留。');
        }
      }
      finally { setBusy(false); }
    }}>{busy ? '正在提交…' : '移到回收站'}</button>}
    {error && <p role="alert">{error}</p>}{inspection && <LibraryReferences inspection={inspection} />}
  </div></section>;
}

export function LibraryDeletionApproval({ details }: { details: DeletionDetails }) {
  const execution = details.execution;
  return <section className="approval-deletion"><p>固定版本 <code>{details.id}</code> · 用户库</p>
    <details><summary>内容摘要 · {details.contentHash.slice(0, 12)}…</summary><code>{details.contentHash}</code></details>
    <LibraryReferences inspection={execution.error ?? details.inspection} />
    {execution.error?.message && <p role="alert">{execution.error.message}</p>}
    <p className="approval-deletion-coverage">检查范围：全部素材与场景版本、历史验证报告、录制、启动中及活动会话。尚未实现的训练存储若含内容，会阻止删除。</p>
    <p role="status">{executionText[execution.status]}</p>
  </section>;
}
