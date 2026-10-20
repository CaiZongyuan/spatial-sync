import { Link } from '@tanstack/react-router';
import { useState } from 'react';
import { ApiError } from '../lib/api';
import { requestLibraryDeletion, type DeletionDetails, type DeletionExecution, type ReferenceInspection } from '../lib/library-deletions';
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

export function LibraryDeletionRequest({ resource, id, root }: { resource: 'asset' | 'scene'; id: string; root: 'builtin' | 'user' }) {
  const [busy, setBusy] = useState(false);
  const [approval, setApproval] = useState<string>();
  const [error, setError] = useState<string>();
  const [inspection, setInspection] = useState<Partial<ReferenceInspection>>();
  return <section className="library-deletion-panel" aria-label="删除库版本"><header><h2>删除版本</h2></header><div>
    <p>仅申请删除 <code>{id}</code>，由人在待审批页决定。</p>
    {root === 'builtin' ? <p>内置库只读，随平台版本发布。</p> : approval ? <p role="status">请求已提交，版本尚未删除。<Link to="/approvals">查看待审批 ↗</Link><small><code>{approval}</code></small></p> : <button disabled={busy} onClick={async () => {
      setBusy(true); setError(undefined); setInspection(undefined);
      try { const value = await requestLibraryDeletion(resource, id); setApproval(value.approvalId); }
      catch (failure) {
        setError((failure as Error).message);
        if (failure instanceof ApiError && failure.detail && typeof failure.detail === 'object') {
          const detail = failure.detail as Partial<ReferenceInspection> & { code?: string };
          setInspection(detail);
          if (detail.code === 'version_in_use') setError('此版本仍被引用，无法申请删除。');
          if (detail.code === 'reference_inspection_incomplete') setError('引用检查未完成，版本会保留。');
        }
      }
      finally { setBusy(false); }
    }}>{busy ? '正在检查引用…' : '申请删除此版本'}</button>}
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
