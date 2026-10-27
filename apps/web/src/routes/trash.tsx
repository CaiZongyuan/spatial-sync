import { createFileRoute, Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { listLibraryRemovals, type LibraryRemoval } from '../lib/library-deletions';
import { RemovalResult } from '../components/LibraryDeletion';

export const Route = createFileRoute('/trash')({ component: LibraryTrash });

function LibraryTrash() {
  const [records, setRecords] = useState<LibraryRemoval[]>(), [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  const [cursors, setCursors] = useState<string[]>([]);
  const [pagination, setPagination] = useState<{ total: number; nextCursor: string | null }>();
  const cursor = cursors.at(-1);
  useEffect(() => {
    const controller = new AbortController();
    setRecords(undefined);
    void listLibraryRemovals(controller.signal, cursor).then(value => {
      if (!controller.signal.aborted) { setRecords(value.deletions); setPagination(value.pagination); setError(undefined); }
    })
      .catch(error => { if (!controller.signal.aborted) setError((error as Error).message); });
    return () => controller.abort();
  }, [attempt, cursor]);
  return <main className="approvals-page"><header className="approvals-page-heading"><h1>回收站</h1>
    <p>保留已撤下版本的原始包；恢复会使用原来的版本身份和字节。</p>
    <button onClick={() => setAttempt(value => value + 1)}>刷新</button> <Link to="/scenes">场景库</Link> · <Link to="/assets">素材库</Link>
  </header>{error && <p role="alert">{error}</p>}
    <nav aria-label="回收站分页"><button disabled={!cursors.length} onClick={() => setCursors(value => value.slice(0, -1))}>上一页</button>
      <span> 第 {cursors.length + 1} 页 · 共 {pagination?.total ?? '—'} 项 </span>
      <button disabled={!pagination?.nextCursor} onClick={() => { if (pagination?.nextCursor) setCursors(value => [...value, pagination.nextCursor!]); }}>下一页</button>
    </nav>
    {!records ? <p role="status">正在读取回收站…</p> : records.length === 0 ? <p>回收站为空。</p> : records.map(record =>
      <article className="approval-card" key={record.id}><h2>{record.versionId}</h2><code>{record.id}</code>
        <RemovalResult record={record} update={value => setRecords(previous => previous?.map(item => item.id === value.id ? value : item))} />
      </article>)}
  </main>;
}
