import { useCallback, useEffect, useRef, useState } from 'react';
import { request } from '../lib/api';
import { dollars, priceFields, priceLabels } from '../lib/model-resources';
import type { LlmModel, PriceTable } from '../lib/model-resources';
import { ResourceTabs } from './ResourceTabs';

function PriceEditor({ models, table, saved, cancel }: {
  models: LlmModel[]; table: PriceTable; saved: (table: PriceTable) => void; cancel: () => void;
}) {
  const [entries, setEntries] = useState(() => {
    const existing = new Map(table.models.map(price => [`${price.provider}/${price.modelId}`, price]));
    for (const model of models) if (!existing.has(model.ref)) existing.set(model.ref, {
      provider: model.provider, modelId: model.modelId,
      inputUsdPerMillion: null, outputUsdPerMillion: null, cacheReadUsdPerMillion: null,
    });
    return [...existing.values()];
  });
  const [gpus, setGpus] = useState(table.gpus);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <form className="resource-price-editor" aria-label="编辑价格表" onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      saved(await request<PriceTable>('/prices', { method: 'PUT', headers: { 'X-Actor': 'user:local' },
        body: JSON.stringify({ revision: table.revision, models: entries, gpus }) }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }}>
    <p className="resource-note">填写真实费率；留空表示价格未知，0 表示免费。金额以美元估算。</p>
    <div className="resource-table-scroll"><table>
      <caption className="sr-only">每百万 Token 的价格（美元）</caption>
      <thead><tr><th>模型</th>{priceLabels.map(label => <th key={label}>{label} $/MTok</th>)}</tr></thead>
      <tbody>{entries.map((price, index) => <tr key={`${price.provider}/${price.modelId}`}>
        <td><code>{price.provider}/{price.modelId}</code></td>
        {priceFields.map((field, column) => <td key={field}><input type="number" min="0" step="any"
          aria-label={`${price.modelId} ${priceLabels[column]} ($/MTok)`} value={price[field] ?? ''}
          disabled={busy} placeholder="未知" onChange={event => {
            const value = event.target.value === '' ? null : Number(event.target.value);
            setEntries(current => current.map((entry, row) => row === index ? { ...entry, [field]: value } : entry));
          }} /></td>)}
      </tr>)}</tbody>
    </table></div>
    {!entries.length && <p className="resource-note">先用 <code>lw model add &lt;provider&gt;/&lt;model_id&gt;</code> 登记 LLM 模型。</p>}
    <div className="resource-editor-heading"><h3>GPU 小时价格</h3><button type="button" disabled={busy}
      onClick={() => setGpus(current => [...current, { nodeId: '', usdPerHour: null }])}>添加 GPU 费率</button></div>
    {gpus.map((price, index) => <div className="resource-gpu-price" key={index}>
      <label>节点名称<input aria-label={`GPU ${index + 1} 节点名称`} required value={price.nodeId} disabled={busy}
        onChange={event => setGpus(current => current.map((entry, row) => row === index ? { ...entry, nodeId: event.target.value } : entry))} /></label>
      <label>美元 / GPU 小时<input aria-label={`GPU ${index + 1} 美元每小时`} type="number" min="0" step="any"
        value={price.usdPerHour ?? ''} disabled={busy} placeholder="未知"
        onChange={event => { const value = event.target.value === '' ? null : Number(event.target.value);
          setGpus(current => current.map((entry, row) => row === index ? { ...entry, usdPerHour: value } : entry)); }} /></label>
      <button type="button" disabled={busy} aria-label={`移除 GPU ${index + 1} 费率`}
        onClick={() => setGpus(current => current.filter((_, row) => row !== index))}>移除</button>
    </div>)}
    {error && <p className="resource-error" role="alert">{error}。可取消编辑并刷新，再重新保存。</p>}
    <div className="resource-form-actions"><button className="primary" disabled={busy} type="submit">{busy ? '正在保存…' : '保存价格表'}</button>
      <button disabled={busy} type="button" onClick={cancel}>取消编辑</button></div>
  </form>;
}

export function ModelResources() {
  const [data, setData] = useState<{ models: LlmModel[]; names: string[]; prices: PriceTable }>();
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState('');
  const pending = useRef<{ controller: AbortController; timer?: ReturnType<typeof setTimeout> } | undefined>(undefined);
  const cancelRead = useCallback(() => {
    const read = pending.current;
    pending.current = undefined;
    if (read) { clearTimeout(read.timer); read.controller.abort(); }
  }, []);
  const load = useCallback(async () => {
    cancelRead();
    const read: NonNullable<typeof pending.current> = { controller: new AbortController() };
    pending.current = read;
    read.timer = setTimeout(() => {
      if (pending.current !== read) return;
      cancelRead(); setError('读取模型与价格超时，请重试');
    }, 8000);
    setError('');
    try {
      const options = { signal: read.controller.signal };
      const [models, vault, prices] = await Promise.all([
        request<{ models: LlmModel[] }>('/models', options), request<{ names: string[] }>('/vault', options), request<PriceTable>('/prices', options),
      ]);
      if (pending.current === read) setData({ models: models.models, names: vault.names, prices });
    } catch (cause) {
      if (pending.current === read) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      clearTimeout(read.timer); read.controller.abort();
      if (pending.current === read) pending.current = undefined;
    }
  }, [cancelRead]);
  useEffect(() => { void load(); return cancelRead; }, [load, cancelRead]);
  const prices = new Map(data?.prices.models.map(price => [`${price.provider}/${price.modelId}`, price]));
  return <main className="model-resources">
    <header className="page-heading"><div><h1>资源</h1><p>算力节点、LLM 与机器人模型、机器人本体及其控制器</p></div>
      <button type="button" disabled={editing} onClick={() => { setNotice(''); void load(); }}>刷新</button></header>
    <ResourceTabs current="models" modelCount={data?.models.length} />
    {error && <p className="resource-error" role="alert">{error}</p>}
    {!data && !error && <p role="status">正在读取模型与价格表…</p>}
    {notice && <p className="resource-save-notice" role="status">{notice}</p>}
    <div className="resource-panels">
      <section className="resource-panel" aria-labelledby="llm-models-title">
        <header><h2 id="llm-models-title">LLM 模型</h2><div><span className="resource-note">价格表由人配置</span>
          {data && <button type="button" disabled={!data.prices.canEdit || editing} onClick={() => { cancelRead(); setEditing(true); setNotice(''); }}>编辑价格表</button>}</div></header>
        <div className="resource-panel-body">
          <div className="resource-table-scroll"><table data-testid="llm-models-table">
            <caption className="sr-only">已登记的 LLM 模型</caption>
            <thead><tr><th>提供方</th><th>model_id</th><th>默认 thinking</th><th>凭据</th><th>健康</th>
              <th className="numeric">延迟 p50</th><th className="numeric">价格 $/MTok · 输入 / 输出 / 缓存读</th><th>使用者</th></tr></thead>
            <tbody>{data?.models.map(model => <tr key={model.ref}>
              <td>{model.provider}</td><td><code>{model.modelId}</code>{model.isDefault && <span className="resource-tag">默认</span>}</td>
              <td>{model.defaultThinking === 'default' ? '提供方默认' : model.defaultThinking}</td>
              <td>{model.credentialName ? <><code>vault:{model.credentialName}</code> {data.names.includes(model.credentialName)
                ? <span className="credential-mask" aria-label="凭据已配置，值已隐藏">••••••••</span>
                : <span className="resource-note">名称缺失</span>}</> : <span className="resource-note">未配置</span>}</td>
              <td><span className="resource-unknown" title="尚无已接入的适配器请求记录">未测量</span></td>
              <td className="numeric">{model.latencyP50Ms == null ? '—' : `${model.latencyP50Ms.toFixed(0)} ms`}</td>
              <td className="numeric">{priceFields.map(field => dollars(prices.get(model.ref)?.[field])).join(' / ')}</td>
              <td className="resource-note">{model.users?.length ? model.users.join('、') : '未接入'}</td>
            </tr>)}
              {data && !data.models.length && <tr><td colSpan={8} className="resource-empty">尚未登记 LLM 模型。用 <code>lw model add &lt;provider&gt;/&lt;model_id&gt;</code> 添加。</td></tr>}
            </tbody>
          </table></div>
          <div className="resource-vault-note"><span aria-hidden="true">ⓘ</span><p>凭据存于本机 Vault，界面只显示名称与固定掩码，不会写入 Trace、日志或录制。
            用 <code>lw vault set &lt;name&gt;</code> 在终端隐藏输入；模型与 thinking 固定，改进只改 Harness。</p></div>
          <p className="resource-note">* 未配置价格，不估算金额。健康与延迟来自实际请求记录，尚无记录时显示未知。</p>
          {data?.prices.gpus.length ? <p className="resource-note">GPU 价格：{data.prices.gpus.map(price => `${price.nodeId} ${dollars(price.usdPerHour)}/小时`).join(' · ')}</p> : null}
          {editing && data && <PriceEditor models={data.models} table={data.prices} cancel={() => setEditing(false)} saved={table => {
            cancelRead();
            setData(current => current && { ...current, prices: table }); setEditing(false); setNotice('价格表已保存，修改已记录操作者与时间。');
          }} />}
          {!!data?.prices.audit.length && <details className="resource-price-audit"><summary>价格修改记录 · {data.prices.audit.length}</summary>
            <ol>{[...data.prices.audit].reverse().map(entry => <li key={entry.revision}>修订 {entry.revision} · <code>{entry.actor.kind}:{entry.actor.name}</code> · <time dateTime={entry.at}>{new Date(entry.at).toLocaleString()}</time></li>)}</ol>
          </details>}
        </div>
      </section>
      <section className="resource-panel is-unconnected" aria-labelledby="robot-models-title">
        <header><h2 id="robot-models-title">机器人模型目录</h2><span className="resource-unknown">未接入</span></header>
        <div className="resource-panel-body"><div className="resource-table-scroll"><table>
          <thead><tr>{['模型', '类型', '来源', '许可', '支持本体', '动作空间', '推理算力', '状态'].map(title => <th key={title}>{title}</th>)}</tr></thead>
          <tbody><tr><td colSpan={8} className="resource-empty">机器人模型目录尚未接入，当前没有可展示的登记数据。</td></tr></tbody>
        </table></div><p className="resource-note">目录用于登记机器人基础模型与策略架构。VLA 仅登记，v1 不在平台内推理；许可与参数以来源为准。</p></div>
      </section>
    </div>
  </main>;
}
