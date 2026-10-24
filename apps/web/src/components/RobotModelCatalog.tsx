import type { useRobotResources } from '../lib/robot-resources';
import '../robot-resources.css';

export function RobotModelCatalog({ query }: { query: ReturnType<typeof useRobotResources> }) {
  return <section className="resource-panel" aria-labelledby="robot-models-title">
    <header><h2 id="robot-models-title">机器人模型目录</h2><span className="resource-tag">{query.data ? `${query.data.models.length} 个登记` : '读取中'}</span></header>
    <div className="resource-panel-body">
      {query.error && <p className="resource-error" role="alert">{query.error} <button onClick={query.refresh}>重读机器人目录</button></p>}
      <div className="resource-table-scroll"><table aria-label="机器人模型目录"><thead><tr>{['模型 / 参数', '类型', '来源', '许可', '支持本体', '动作空间', '推理算力', '平台支持 / 质量'].map(name => <th key={name}>{name}</th>)}</tr></thead><tbody>
        {query.data?.models.map(model => <tr key={model.id} data-robot-model={model.id}><th scope="row">{model.title}<small><code>{model.id}</code></small>
          <small>{model.parameters == null ? '无神经网络参数声明' : `${model.parameters.toLocaleString()} 参数（上游）`}</small></th>
          <td>{model.family}</td><td><code>{model.source}</code>{model.sources.map(source => <small key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a><span> · 核对 {source.retrievedOn}</span></small>)}</td>
          <td>{model.license ?? '未单独声明'}</td><td>{model.embodiments.length ? model.embodiments.join(' · ') : '无平台绑定'}
            <small>上游：{model.upstreamEmbodiments.join(' · ')}</small></td><td>{model.actionSpace}</td><td>{model.compute.inference}</td>
          <td><span className={`resource-tag ${model.runnable ? '' : 'resource-unknown'}`}>{model.runnable ? '支持加载' : '仅登记 · v1 不推理'}</span>
            <small>{model.quality.status === 'not-evaluated' ? '质量未评估' : `评估引用 ${model.quality.evaluationId}`}</small>
            <details><summary>能力与环境要求</summary><p>{model.capability.notes}</p><ul>{model.capability.requirements.map(value => <li key={value}>{value}</li>)}</ul></details></td></tr>)}
        {!query.data && <tr><td colSpan={8} className="resource-empty">{query.error ? '目录暂不可用。' : '正在读取机器人模型…'}</td></tr>}
      </tbody></table></div>
      <p className="resource-note">支持加载表示平台已有对应程序与配置接口；运行仍需满足环境要求。它不代表本机已准备就绪、通过质量评估或获准提升。VLA 的上游能力与本平台绑定分别记录。</p>
    </div>
  </section>;
}

