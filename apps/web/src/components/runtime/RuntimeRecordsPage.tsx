import { Link, useNavigate, useRouter } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { importRuntimeCapture, listRuntimeSessions, readRuntimeSession, runtimeChanged, runtimeCostSource,
  runtimeCount, runtimeMoney, runtimeStatus, runtimeTime, type RuntimeAssociations, type RuntimeEvent,
  type RuntimeFormat, type RuntimeImportResult, type RuntimeSessionDetail, type RuntimeSessionSummary } from '../../lib/runtime-records';
import './runtime-records.css';

function ImportCapture() {
  const [file, setFile] = useState<File>(), [format, setFormat] = useState<RuntimeFormat>('claude-code-stream-json');
  const [sourceLabel, setSourceLabel] = useState(''), [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false), [receipt, setReceipt] = useState<RuntimeImportResult>();
  const [refs, setRefs] = useState({ agentId: '', harnessVersion: '', planId: '', planRevision: '', goalId: '' });
  const [clear, setClear] = useState(false);
  const active = useRef<AbortController | undefined>(undefined), alive = useRef(true);
  const navigate = useNavigate(), router = useRouter();
  useEffect(() => { alive.current = true; return () => { alive.current = false; active.current?.abort(); }; }, []);
  const submit = async () => {
    if (!file || busy) return;
    setBusy(true); setError(undefined); setReceipt(undefined);
    const controller = new AbortController(); active.current = controller;
    try {
      if (Boolean(refs.planId) !== Boolean(refs.planRevision)) throw new Error('Plan 关联须同时填写 ID 和精确修订。');
      if (refs.harnessVersion && !refs.agentId) throw new Error('Harness 版本须关联明确的 Agent。');
      const associations: RuntimeAssociations = {};
      for (const key of ['agentId', 'planId', 'goalId'] as const) if (refs[key].trim()) associations[key] = refs[key].trim();
      for (const key of ['harnessVersion', 'planRevision'] as const) if (refs[key]) {
        const number = Number(refs[key]);
        if (!Number.isSafeInteger(number) || number < 1) throw new Error('版本和修订须为正整数。');
        associations[key] = number;
      }
      if (clear && Object.keys(associations).length) throw new Error('清除关联与填写新关联只能选择一种。');
      const content = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer());
      const result = await importRuntimeCapture({ format, content, sourceLabel: sourceLabel.trim() || undefined,
        ...(clear || Object.keys(associations).length ? { associations } : {}) }, controller.signal);
      if (!alive.current) return;
      setReceipt(result);
      if (router.state.location.pathname.startsWith('/runtime-sessions')) {
        await navigate({ to: '/runtime-sessions/$id', params: { id: result.session.id } });
      }
    } catch (failure) { if (alive.current && !controller.signal.aborted) setError((failure as Error).message); }
    finally { if (alive.current) setBusy(false); active.current = undefined; }
  };
  return <section className="panel runtime-import" aria-label="导入运行记录"><header><h2>导入已有运行记录</h2><span>仅保存与展示</span></header>
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <div className="runtime-import-fields"><label>原生格式<select aria-label="运行记录格式" value={format} onChange={event => setFormat(event.target.value as RuntimeFormat)}>
        <option value="claude-code-stream-json">Claude Code · stream-json</option><option value="claude-code-session-jsonl">Claude Code · session JSONL</option></select></label>
        <label>选择或重新选择文件<input aria-label="原生运行记录文件" type="file" accept=".jsonl,.json,application/json,application/x-ndjson,text/plain" disabled={busy}
          onChange={event => { const value = event.target.files?.[0]; setFile(value); setSourceLabel(value?.name ?? '');
            setRefs({ agentId: '', harnessVersion: '', planId: '', planRevision: '', goalId: '' }); setClear(false); setError(undefined); }} /></label>
        <label>来源标签<input aria-label="运行记录来源标签" value={sourceLabel} onChange={event => setSourceLabel(event.target.value)} maxLength={256} /></label>
      </div>
      <p>选择追加后的同一份文件可刷新已有记录。原生身份与计量范围由记录决定，重复导入保持幂等。</p>
      <details><summary>可选：关联已有平台资源</summary><p>仅建立记录关联，不代表 Plan 已获批准、执行或完成。未填写时保留已有明确关联；新记录保持未关联。</p>
        <div className="runtime-import-fields">{([['agentId', 'Agent ID', 'agent/name'], ['harnessVersion', 'Harness 版本', '1'], ['planId', 'Plan ID', 'plan-…'], ['planRevision', 'Plan 精确修订', '1'], ['goalId', 'Goal ID', 'goal/name']] as const).map(([key, label, placeholder]) =>
          <label key={key}>{label}<input aria-label={`记录关联${label}`} value={refs[key]} placeholder={placeholder}
            inputMode={key === 'harnessVersion' || key === 'planRevision' ? 'numeric' : undefined}
            onChange={event => setRefs({ ...refs, [key]: event.target.value })} /></label>)}</div>
        <label className="runtime-clear"><input type="checkbox" checked={clear} onChange={event => setClear(event.target.checked)} />明确清除已有全部关联</label>
      </details>
      <button type="submit" disabled={!file || busy}>{busy ? '正在导入…' : '导入 / 刷新文件'}</button>
      {error && <p role="alert">导入失败：{error}</p>}
      {receipt && <p role="status">{receipt.idempotent ? '相同记录已存在。' : '记录已保存。'}新增 {receipt.addedEvents} 条，更新 {receipt.updatedEvents} 条。</p>}
    </form>
  </section>;
}

function EventContent({ event }: { event: RuntimeEvent }) {
  if (event.content == null) return <p className="runtime-muted">此事件没有可展示内容。</p>;
  if (typeof event.content === 'string') return <pre>{event.content}</pre>;
  return <>{event.content.map((block, index) => <div className="runtime-block" key={index}>
    {block.type === 'text' && typeof block.text === 'string' ? <pre>{block.text}</pre> :
      <details open={block.type === 'tool_use' || block.type === 'tool_result'}><summary>{typeof block.type === 'string' ? block.type : '结构化内容'}{typeof block.name === 'string' && ` · ${block.name}`}</summary>
        <pre>{JSON.stringify(block, null, 2)}</pre></details>}
  </div>)}</>;
}

function RuntimeDetail({ record }: { record: RuntimeSessionDetail }) {
  const usage = record.usage, refs = record.associations;
  return <section data-testid="runtime-detail" className="runtime-detail">
    <header className="runtime-detail-heading"><div><span className="eyebrow">IMPORTED RUNTIME DATA</span><h2>{record.sourceLabel ?? record.nativeSessionId}</h2><code>{record.id}</code></div>
      <Link to="/cost" search={{ range: '7d', ...(refs.agentId ? { agent: refs.agentId } : {}),
        ...(refs.planId && refs.planRevision ? { plan: `${refs.planId}@${refs.planRevision}` } : {}),
        ...(refs.goalId ? { goal: refs.goalId } : {}) }}>打开成本总览</Link></header>
    <div className="runtime-stat-grid"><article><span>运行时状态</span><strong>{runtimeStatus[record.status]}</strong></article>
      <article><span>总 Token / 请求</span><strong>{runtimeCount(usage.tokens)} / {runtimeCount(usage.requests)}</strong><small>运行时轮次 {runtimeCount(usage.turns)}</small></article>
      <article><span>运行时费用</span><strong>{runtimeMoney(usage.llmUsd)}</strong><small>{runtimeCostSource[usage.costSource]}</small></article>
      <article><span>事件 / 消息 / 工具调用</span><strong>{record.eventCount} / {record.messageCount} / {record.toolCallCount}</strong></article></div>
    <section className="panel"><h3>来源与关联</h3><dl className="runtime-facts">
      <div><dt>运行时 / 原生格式</dt><dd>{record.runtime} · {record.runtimeVersion ?? '版本未提供'} · {record.format}</dd></div>
      <div><dt>原生会话 / Capture</dt><dd><code>{record.nativeSessionId}</code><br /><code>{record.captureId}</code></dd></div>
      <div><dt>原生记录时间</dt><dd>{runtimeTime(record.createdAt)} — {runtimeTime(record.updatedAt)}</dd></div>
      <div><dt>导入 / 最近导入</dt><dd>{runtimeTime(record.importedAt)} — {runtimeTime(record.lastImportedAt)}</dd></div>
      <div><dt>模型 / 思考模式</dt><dd>{record.models.join(', ') || '未提供'} · {record.thinking ?? '未提供'}</dd></div>
      <div><dt>Agent / Harness 关联</dt><dd>{refs.agentId ?? '未关联'}{refs.harnessVersion != null && ` · v${refs.harnessVersion}`}</dd></div>
      <div><dt>Plan 关联</dt><dd>{refs.planId ? `${refs.planId}${refs.planRevision != null ? `@${refs.planRevision}` : ''}` : '未关联'}</dd></div>
      <div><dt>Goal 关联</dt><dd>{refs.goalId ?? '未关联'}</dd></div>
      <div><dt>内容摘要</dt><dd><code>{record.contentHash}</code></dd></div>
    </dl><p className="runtime-muted">消息、工具和运行时结束状态均为导入数据，不生成平台评分、审批或设备动作。</p></section>
    <section className="panel" aria-label="运行时用量"><h3>原样报告的用量</h3><table aria-label="Runtime Token 明细"><thead><tr><th>输入</th><th>输出</th><th>缓存读取</th><th>缓存写入</th><th>请求</th><th>轮次</th></tr></thead>
      <tbody><tr><td>{runtimeCount(usage.inputTokens)}</td><td>{runtimeCount(usage.outputTokens)}</td><td>{runtimeCount(usage.cacheReadTokens)}</td><td>{runtimeCount(usage.cacheWriteTokens)}</td><td>{runtimeCount(usage.requests)}</td><td>{runtimeCount(usage.turns)}</td></tr></tbody></table>
      <p className="runtime-muted">计量范围：{usage.scope} · {usage.basis}。缺失字段保留未知，轮次单列。费用以运行时回执为准。</p>
      <ul className="runtime-evidence">{record.artifacts.map(artifact => <li key={artifact.id}><a href={artifact.url} target="_blank" rel="noreferrer">{artifact.kind === 'runtime-transcript' ? '规范化消息 / 工具记录' : '用量与费用来源'}</a>
        <small>{artifact.source} · {artifact.size.toLocaleString()} B · {artifact.sha256.slice(0, 12)}</small></li>)}</ul>
    </section>
    <section className="panel"><h3>消息与工具记录</h3><div className="runtime-events">{record.events.map(event => <article key={event.id} className={`runtime-event is-${event.kind}`}>
      <header><strong>{event.kind === 'tool' ? '工具记录' : event.kind === 'result' ? '运行时结果回执' : event.kind === 'metadata' ? '元数据' : event.role ?? '消息'}</strong>
        <small>{event.nativeType} · {runtimeTime(event.timestamp)}{event.model && ` · ${event.model}`}</small></header>
      <EventContent event={event} /><details className="runtime-event-identity"><summary>原生身份与内容摘要</summary><code>{event.nativeId ?? '未提供'} · {event.contentHash}</code></details>
    </article>)}</div></section>
  </section>;
}

export function RuntimeRecordsPage({ id, query = '', onQuery }: { id?: string; query?: string; onQuery: (value: string) => void }) {
  const [sessions, setSessions] = useState<RuntimeSessionSummary[]>(), [record, setRecord] = useState<RuntimeSessionDetail>();
  const [error, setError] = useState<string>(), [retry, setRetry] = useState(0);
  useEffect(() => {
    let disposed = false, active: AbortController | undefined;
    setRecord(undefined); setError(undefined);
    const read = async () => {
      if (active) return;
      const controller = new AbortController(); active = controller;
      try {
        const [list, detail] = await Promise.all([listRuntimeSessions(query, controller.signal), id ? readRuntimeSession(id, controller.signal) : undefined]);
        if (!disposed) { setSessions(list); setRecord(detail); setError(undefined); }
      } catch (failure) { if (!disposed) setError((failure as Error).message); }
      finally { active = undefined; }
    };
    const refresh = () => void read();
    void read(); const timer = setInterval(refresh, 5000); window.addEventListener(runtimeChanged, refresh);
    return () => { disposed = true; active?.abort(); clearInterval(timer); window.removeEventListener(runtimeChanged, refresh); };
  }, [id, query, retry]);
  return <main className="runtime-page" data-testid="runtime-records-page"><header className="runtime-heading"><div><span className="eyebrow">EXTERNAL RUNTIME RECORDS</span><h1>运行记录</h1><p>已有 Claude Code 会话、消息与计量数据</p></div>
    <button onClick={() => setRetry(value => value + 1)}>刷新已存记录</button></header>
    <ImportCapture />{error && <p role="alert">记录暂不可用：{error}</p>}
    <section className="panel"><header className="runtime-list-heading"><h2>Runtime 会话</h2><label>搜索<input aria-label="搜索 Runtime 记录" value={query} onChange={event => onQuery(event.target.value)} /></label></header>
      <div className="runtime-table-scroll"><table aria-label="Runtime 会话列表"><thead><tr><th>来源 / 原生会话</th><th>状态</th><th>模型</th><th>消息 / 工具</th><th>Token / 费用</th><th>原生时间 / 导入时间</th></tr></thead><tbody>
        {sessions?.map(session => <tr key={session.id} className={id === session.id ? 'is-selected' : ''}><th><Link to="/runtime-sessions/$id" params={{ id: session.id }} search={{ q: query || undefined }}>{session.sourceLabel ?? session.nativeSessionId}</Link><code>{session.nativeSessionId}</code><small>{session.runtime}</small></th>
          <td>{runtimeStatus[session.status]}</td><td>{session.models.join(', ') || '未提供'}</td><td>{session.messageCount} / {session.toolCallCount}</td>
          <td>{runtimeCount(session.usage.tokens)} Token<small>{runtimeMoney(session.usage.llmUsd)} · {runtimeCostSource[session.usage.costSource]}</small></td>
          <td>{runtimeTime(session.updatedAt)}<small>导入 {runtimeTime(session.importedAt)}</small></td></tr>)}
      </tbody></table></div>{sessions && !sessions.length && <p>暂无符合条件的运行记录。导入已有原生文件后在此查看。</p>}
      {!sessions && !error && <p role="status">正在读取运行记录…</p>}</section>
    {id && (record?.id === id ? <RuntimeDetail record={record} /> : !error && <p role="status">正在读取记录详情…</p>)}
  </main>;
}
