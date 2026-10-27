import { createFileRoute, Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { listLibraryRemovals, type LibraryRemoval } from '../lib/library-deletions';
import { RemovalResult } from '../components/LibraryDeletion';

export const Route = createFileRoute('/trash')({ component: LibraryTrash });

function LibraryTrash() {
  const [records, setRecords] = useState<LibraryRemoval[]>(), [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void listLibraryRemovals(controller.signal).then(value => { setRecords(value.deletions); setError(undefined); })
      .catch(error => { if (!controller.signal.aborted) setError((error as Error).message); });
    return () => controller.abort();
  }, [attempt]);
  return <main className="approvals-page"><header className="approvals-page-heading"><h1>回收站</h1>
    <p>保留已撤下版本的原始包；恢复会使用原来的版本身份和字节。</p>
    <button onClick={() => setAttempt(value => value + 1)}>刷新</button> <Link to="/scenes">场景库</Link> · <Link to="/assets">素材库</Link>
  </header>{error && <p role="alert">{error}</p>}
    {!records ? <p role="status">正在读取回收站…</p> : records.length === 0 ? <p>回收站为空。</p> : records.map(record =>
      <article className="approval-card" key={record.id}><h2>{record.versionId}</h2><code>{record.id}</code>
        <RemovalResult record={record} update={value => setRecords(previous => previous?.map(item => item.id === value.id ? value : item))} />
      </article>)}
  </main>;
}
