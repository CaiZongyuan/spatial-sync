import { useEffect, useState } from 'react';
import { request } from '../../lib/api';

interface Snapshot {
  id: string; contentHash: string; codeHash: string; configHash: string;
  metadata: { title: string; entrypoint: string; implements: string; config: Record<string, unknown> };
  files: Record<string, { sha256: string; size: number }>;
}

export function ProgramReview({ intent }: { intent: { id: string; contentHash: string } }) {
  const [snapshot, setSnapshot] = useState<Snapshot>(), [error, setError] = useState<string>();
  const match = /^program\/robot_arm\.script\.([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)$/.exec(intent.id);
  const path = match ? `/programs/${encodeURIComponent(match[1])}/${match[2]}` : undefined;
  useEffect(() => {
    setSnapshot(undefined); setError(undefined);
    if (!path) { setError('程序版本引用无效'); return; }
    const controller = new AbortController();
    void request<Snapshot>(path, { signal: controller.signal }).then(value => {
      if (value.contentHash !== intent.contentHash) throw new Error('程序快照与计划哈希不同');
      if (!controller.signal.aborted) setSnapshot(value);
    }).catch(failure => { if (!controller.signal.aborted) setError((failure as Error).message); });
    return () => controller.abort();
  }, [path, intent.contentHash]);
  return <section className="program-review" aria-label="冻结脚本代码与配置"><h4>冻结脚本代码与配置</h4>
    <code>{intent.id}</code><p>内容哈希：<code>{intent.contentHash}</code></p>
    {error ? <p role="alert">程序快照不可用：{error}</p> : !snapshot ? <p role="status">正在读取冻结代码…</p> : <>
      <p>{snapshot.metadata.title} · {snapshot.metadata.implements}</p>
      <p>入口：<code>{snapshot.metadata.entrypoint}</code></p>
      <details><summary>代码与配置哈希</summary><p>代码：<code>{snapshot.codeHash}</code></p><p>配置：<code>{snapshot.configHash}</code></p></details>
      <details><summary>本版本配置</summary><pre>{JSON.stringify(snapshot.metadata.config, null, 2)}</pre></details>
      <ul>{Object.entries(snapshot.files).map(([name, file]) => <li key={name}>
        <a href={`/api/v1${path}/files/${name.split('/').map(encodeURIComponent).join('/')}`} target="_blank" rel="noreferrer">读取 {name}</a>
        <small> · {file.size} 字节 · {file.sha256.slice(0, 12)}</small>
      </li>)}</ul>
    </>}
  </section>;
}
