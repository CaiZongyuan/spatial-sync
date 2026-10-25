import { Link } from '@tanstack/react-router';
import { useRobotResources, type RobotEmbodiment, type RobotModel } from '../lib/robot-resources';
import { AssetModelViewport } from './AssetModelViewport';
import { ResourceTabs } from './ResourceTabs';
import '../resources.css';
import '../assets.css';
import '../robot-resources.css';

function RobotCard({ robot, models }: { robot: RobotEmbodiment; models: RobotModel[] }) {
  const current = robot.currentController, binding = robot.bindings[0];
  const model = models.find(item => item.id === (current?.robotModel ?? binding?.robotModel));
  const [kind, assetName] = robot.asset.split('@')[0].split('/'), assetRevision = robot.asset.split('@')[1];
  return <article className={`resource-panel robot-card ${robot.kind === 'real' ? 'is-unconnected' : ''}`} data-embodiment={robot.id}>
    <header><h2>{robot.title}</h2><span className="resource-tag">{robot.kind === 'real' ? '真机 · 未接入' : 'SIM · 仿真'}</span></header>
    <div className="robot-card-body"><div className="robot-identity">
      {robot.kind === 'sim' && robot.visualUrl ? <AssetModelViewport url={robot.visualUrl} /> : <div className="robot-disconnected-preview">未接入真机<small>尚无真实设备连接与实时状态。</small></div>}
      <dl><div><dt>Embodiment</dt><dd><code>{robot.id}</code></dd></div>
        <div><dt>素材</dt><dd><Link to="/assets/$kind/$name/$rev" params={{ kind, name: assetName, rev: assetRevision }}>{robot.asset} ↗</Link></dd></div>
        {robot.bindings.map(item => <div key={`${item.scene}:${item.entity}`}><dt>场景 / entityKey</dt><dd><Link to="/scenes/$name/$rev" params={{ name: item.scene.split('@')[0], rev: item.scene.split('@')[1] }}>{item.scene} ↗</Link>
          <small><code>{item.entity}</code></small><small>{item.source} · graspMode: {item.graspMode ?? '未声明'}</small><small title={item.sceneContentHash}>场景内容 {item.sceneContentHash}</small></dd></div>)}
        {!robot.bindings.length && <div><dt>绑定</dt><dd>未接入 · 无场景或实况绑定</dd></div>}
      </dl>
    </div><div className="robot-control-stack"><h3>控制器栈</h3>
      <p className="resource-note">{current ? `Controller ${current.controller} · v${current.version}` : binding ? '尚无已提升 Controller。下方展示当前场景已绑定的内置程序。' : '未接入设备或控制器。'}</p>
      <dl><div><dt>Robot Model</dt><dd>{model?.title ?? '未绑定'}<small>{model?.id}</small></dd></div>
        <div><dt>策略版本</dt><dd>{current?.policy ?? '无已提升策略'}</dd></div>
        <div><dt>设备程序</dt><dd><code>{current?.program ?? binding?.program ?? '未接入'}</code></dd></div>
        {current?.programArtifact && <div><dt>脚本版本</dt><dd><code>{current.programArtifact.id}</code><small>{current.programArtifact.contentHash}</small></dd></div>}
        {current && <div><dt>默认部署场景</dt><dd><Link to="/scenes/$name/$rev" params={{ name: current.scene.split('@')[0], rev: current.scene.split('@')[1] }}>{current.scene} →</Link></dd></div>}
        <div><dt>动作空间 / 频率</dt><dd>{current?.action.space ?? model?.actionSpace ?? '未接入'}<small>{current?.action.hz ? `${current.action.hz} Hz` : '频率未单独声明'}</small><small>{current?.action.hzSource ?? current?.action.hzUnknownReason}</small></dd></div>
        <div><dt>观测</dt><dd>{current?.observation ?? (binding ? '内置 Program / PhysicsPort；无策略 obs_spec' : '未接入')}</dd></div>
        <div><dt>评估成绩</dt><dd>{current?.evaluationId ? `评估引用 ${current.evaluationId}` : '暂无 Controller 评估结果'}</dd></div></dl>
      {model && <p className="resource-note">{model.capability.notes}</p>}
      {!!robot.activeSessions?.length && <div className="robot-active-sessions"><h4>活动会话保留的版本</h4>{robot.activeSessions.map(session => <p key={session.sessionId}>
        <Link to="/sessions/$sessionId" params={{ sessionId: session.sessionId }}>{session.sessionId}</Link><small>{session.scene} · {session.controller ? `Controller ${session.controller.id} v${session.controller.version}` : '场景内置程序'}</small>
      </p>)}</div>}
    </div></div>
    <div className="robot-history"><h3>Controller 版本历史</h3>
      {robot.controllerHistory.length ? <div className="resource-table-scroll"><table aria-label={`${robot.id} Controller 历史`}><thead><tr><th>版本</th><th>Robot Model / 策略</th><th>程序</th><th>场景</th><th>评估引用</th></tr></thead><tbody>
        {robot.controllerHistory.map(value => <tr key={value.version}><td>v{value.version}{value.version === current?.version && <small>当前</small>}</td><td>{value.robotModel}<small>{value.policy ?? '无策略'}</small></td><td>{value.program}<small>{value.programArtifact?.id}</small></td><td>{value.scene}</td><td>{value.evaluationId ?? '未提供'}</td></tr>)}
      </tbody></table></div> : <p className="resource-note">尚无已提升版本。策略或脚本通过独立提升流程后，显示实际 Controller 版本与评估记录。</p>}
    </div>
  </article>;
}

export function RobotResourcesPage() {
  const query = useRobotResources();
  return <main className="model-resources robot-resources"><header className="page-heading"><div><h1>资源</h1><p>机器人本体、当前场景绑定与实际控制组合</p></div><button onClick={query.refresh}>刷新机器人</button></header>
    <ResourceTabs current="robots" robotModelCount={query.data?.models.length} embodimentCount={query.data?.embodiments.length} />
    {query.error && <p className="resource-error" role="alert">{query.error} {query.data && '保留上次成功读取的资源。'}</p>}
    {!query.data && !query.error && <p role="status">正在读取机器人本体与控制器…</p>}
    {query.data?.embodiments.map(robot => <RobotCard key={robot.id} robot={robot} models={query.data!.models} />)}
  </main>;
}
