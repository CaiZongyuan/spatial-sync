import { createFileRoute, Link } from '@tanstack/react-router';
import { useCallback, useEffect, useState } from 'react';
import { listLibraryRemovals, type LibraryRemoval } from '../lib/library-deletions';
import { formatBytes } from '../lib/catalog';
import { RemovalResult } from '../components/LibraryDeletion';
import { Icon } from '../components/Icon';
import '../trash.css';

export const Route = createFileRoute('/trash')({ component: LibraryTrash });

const statusLabels: Record<LibraryRemoval['status'], string> = {
  queued: '等待检查', checking: '检查中', committed: '可恢复',
  blocked: '未移入', failed: '未完成', restoring: '恢复中', restored: '已恢复',
};
const dateFormat = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
});

function LibraryTrash() {
  const [records, setRecords] = useState<LibraryRemoval[]>(), [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  const [cursors, setCursors] = useState<string[]>([]);
  const [pagination, setPagination] = useState<{ total: number; nextCursor: string | null }>();
  const cursor = cursors.at(-1);
  const loading = !records && !error;
  const update = useCallback((value: LibraryRemoval) => setRecords(previous =>
    previous?.map(item => item.id === value.id ? value : item)), []);
  useEffect(() => {
    const controller = new AbortController();
    setRecords(undefined); setError(undefined); setPagination(undefined);
    void listLibraryRemovals(controller.signal, cursor).then(value => {
      if (!controller.signal.aborted) { setRecords(value.deletions); setPagination(value.pagination); }
    })
      .catch(error => { if (!controller.signal.aborted) setError((error as Error).message); });
    return () => controller.abort();
  }, [attempt, cursor]);
  return <main className="trash-page">
    <header className="trash-heading">
      <div><span className="trash-eyebrow">LIBRARY / RECOVERY</span><h1>回收站</h1>
        <p>找回撤下的素材与场景，继续使用原来的版本。</p></div>
      <nav className="trash-library-links" aria-label="返回库">
        <Link to="/scenes"><Icon name="scenes" />场景库<Icon name="chevron" /></Link>
        <Link to="/assets"><Icon name="assets" />素材库<Icon name="chevron" /></Link>
      </nav>
    </header>
    <div className="trash-retention-note"><span className="trash-note-icon"><Icon name="restore" /></span>
      <div><strong>原始版本，完整保留</strong><p>恢复会保留原版本号与内容。移入回收站后，原始包仍占用磁盘空间。</p></div>
    </div>
    <section className="trash-panel" aria-labelledby="trash-records-heading">
      <header className="trash-panel-heading"><div><h2 id="trash-records-heading">版本记录</h2>
        <span className="trash-count" aria-label="记录总数">{pagination?.total.toLocaleString() ?? '—'}</span></div>
        <button className="trash-refresh" disabled={loading} onClick={() => setAttempt(value => value + 1)}>
          <Icon name="reload" />{loading ? '读取中…' : '刷新'}</button>
      </header>
      {error ? <div className="trash-feedback" role="alert"><span className="trash-feedback-icon is-error"><Icon name="warning" /></span>
        <h3>暂时无法读取回收站</h3><p>{error}</p><div className="trash-feedback-actions">
          <button onClick={() => setAttempt(value => value + 1)}>重试当前页</button>
          {cursors.length > 0 && <button onClick={() => setCursors([])}>返回第一页</button>}
        </div></div>
      : !records ? <div className="trash-loading" role="status"><span className="sr-only">正在读取回收站…</span>
        {[0, 1, 2].map(value => <div className="trash-skeleton" key={value} aria-hidden="true">
          <span /><div><i /><i /></div><span /></div>)}</div>
      : records.length === 0 ? <div className="trash-feedback">
        <span className="trash-feedback-icon"><Icon name="trash" /></span>
        <span className="trash-eyebrow">ALL CLEAR</span><h3>回收站为空</h3>
        <p>撤下的用户库版本会保留在这里，需要时可以恢复。</p>
        <div className="trash-feedback-actions"><Link to="/scenes" className="button-link">浏览场景库<Icon name="chevron" /></Link>
          <Link to="/assets" className="button-link">浏览素材库<Icon name="chevron" /></Link></div>
      </div> : <div className="trash-records">{records.map(record =>
        <article className="trash-record" key={record.id} aria-label={record.versionId}>
          <header className="trash-record-heading"><span className="trash-resource-icon"><Icon name={record.resource === 'scene' ? 'scenes' : 'assets'} /></span>
            <div className="trash-record-identity"><h3>{record.versionId}</h3><div className="trash-record-meta">
              <span>{record.resource === 'scene' ? '场景' : '素材'}</span><span>提交于 <time dateTime={new Date(record.createdAtMs).toISOString()}>{dateFormat.format(record.createdAtMs)}</time></span>
              <span>保留 {formatBytes(record.retainedBytes)}</span>
            </div></div>
            <span className={`trash-status is-${record.status}`}><i />{statusLabels[record.status]}</span>
          </header>
          <RemovalResult record={record} update={update} compact />
          <details className="trash-record-details"><summary>记录详情<Icon name="chevron" /></summary><dl>
            <div><dt>记录 ID</dt><dd><code>{record.id}</code></dd></div>
            <div><dt>内容哈希</dt><dd><code>{record.contentHash}</code></dd></div>
            <div><dt>保留字节</dt><dd>{record.retainedBytes.toLocaleString()} B</dd></div>
          </dl></details>
        </article>)}</div>}
      <footer className="trash-panel-footer"><p>保留恢复与未完成的操作记录</p>
        <nav aria-label="回收站分页"><button aria-label="上一页" disabled={loading || !cursors.length}
          onClick={() => setCursors(value => value.slice(0, -1))}><Icon name="collapse" /></button>
          <span aria-live="polite">第 {cursors.length + 1} 页{pagination ? ` · 共 ${pagination.total.toLocaleString()} 项` : ''}</span>
          <button aria-label="下一页" disabled={loading || !pagination?.nextCursor}
            onClick={() => { if (pagination?.nextCursor) setCursors(value => [...value, pagination.nextCursor!]); }}><Icon name="chevron" /></button>
        </nav>
      </footer>
    </section>
    <p className="trash-footnote"><Icon name="lock" />仅未被引用的用户库版本可移入回收站；内置库保持只读。</p>
  </main>;
}
