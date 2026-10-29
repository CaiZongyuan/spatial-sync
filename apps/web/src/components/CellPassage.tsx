import { useState, useSyncExternalStore } from 'react';
import type { OperationPlan } from '../lib/protocol';
import { PassageControls } from './PassageControls';
import { PassageEnvironment } from './PassageEnvironment';
import { roundIdentity } from '../lib/cell-passage/round';
import type { SessionClient } from '../lib/stream/client';
import { useEntity } from '../lib/info/hooks';
import { lineage, liveCells, materialState, object, passageContract, passageStage, quantity, rows, text } from '../lib/cell-passage/model';
import '../cell-passage.css';

export function CellPassage({ client, plan, plans = [], selectPlan }: { client: SessionClient; plan?: OperationPlan; plans?: OperationPlan[]; selectPlan?: (id: string) => void }) {
  const metadata = useSyncExternalStore(client.info.subscribeMetadata, client.info.getMetadata);
  const keys = Object.entries(metadata.catalog?.entities ?? {}).filter(([, binding]) => binding.type === passageContract.workcellType).map(([key]) => key);
  const environments = Object.entries(metadata.catalog?.entities ?? {}).filter(([, binding]) => ['passage-incubator/v1', 'passage-cabinet/v1'].includes(binding.type));
  return <>{keys.map(entityKey => <PassageState key={entityKey} {...{ client, entityKey, plan, plans, selectPlan }} ready={metadata.ready} />)}{keys.length > 0 && environments.map(([entityKey, binding]) => <PassageEnvironment key={entityKey} {...{ client, entityKey }} type={binding.type} />)}</>;
}
function PassageState({ client, entityKey, ready, plan, plans, selectPlan }: { client: SessionClient; entityKey: string; ready: boolean; plan?: OperationPlan; plans: OperationPlan[]; selectPlan?: (id: string) => void }) {
  const entity = useEntity(client.info, entityKey);
  const [selected, select] = useState('');
  const { snapshot, vessels, known, reconciled, stale } = materialState(entity);
  const values = entity?.values ?? {};
  const vessel = vessels.find(row => row.id === selected) ?? vessels[0];
  const warning = !ready || !known ? '物料状态未知，等待权威快照' : stale ? '物料状态已过期，等待同步' : !reconciled ? '物料未核对；保留现场，禁止续行' : undefined;
  const families = lineage(vessels);
  const observations = rows(snapshot?.observations).slice(-6).reverse();
  const tip = object(snapshot?.tip);
  const activeStep = plan?.steps.find(step => step.status === 'running') ?? plan?.steps.find(step => step.status === 'failed') ?? plan?.steps.find(step => step.status === 'pending');
  const round = roundIdentity(plan?.id ?? '');
  const parts = round ? plans.filter(item => roundIdentity(item.id)?.roundId === round.roundId).sort((a, b) => {
    const x = roundIdentity(a.id)!, y = roundIdentity(b.id)!; return x.stage - y.stage || x.part - y.part;
  }) : [];
  return <section className="cell-passage" aria-label="细胞传代工作台">
    <header><div><span>实验状态模型 · {client.readOnly ? '录制回放' : '源侧权威状态'}</span><h2>细胞传代</h2></div><p>阶段：{passageStage(activeStep?.id)} · 设备 {text(values.phase)} · 物料修订 {quantity(snapshot?.revision)}</p></header>
    {warning && <p role="alert">{warning}</p>}
    <dl className="passage-summary"><div><dt>控制状态</dt><dd>{text(values.controlMode)} · {values.controlSafe === true ? '源侧已停稳' : values.controlSafe === false ? '尚未停稳' : '安全状态未知'}</dd></div>
      <div><dt>可用吸头</dt><dd>{quantity(values.tipsRemaining)}</dd></div><div><dt>废物剩余容量</dt><dd>{quantity(values.wasteFreeUl)} µL</dd></div>
      <div><dt>在途液体 / 已装吸头</dt><dd>{quantity(values.inFlightUl)} µL / {tip?.id === '' ? '未安装' : text(tip?.id)}</dd></div></dl>
    {round && <div className="passage-round"><h3>本轮分段计划</h3><p>{parts.filter(part => part.status === 'completed').length} / {parts.length} 已显示分段步骤完成；全部七阶段及末态另行核验</p><details><summary>七阶段与分段</summary><ol>{parts.map(part => <li key={part.id}><button onClick={() => selectPlan?.(part.id)}>{part.title} · {part.status}</button></li>)}</ol></details></div>}
    {!client.readOnly && <PassageControls client={client} workcell={entityKey} plan={plan} blocked={warning} selectPlan={selectPlan} />}
    <h3>母孔 → 子孔谱系</h3>
    {families.length ? <ul className="passage-lineage">{families.map(({ mother, children }) => <li key={text(mother.id)}><b>{text(mother.id)} · {text(mother.culture)}</b><span> → </span>{children.length ? children.map(child => <button key={text(child.id)} onClick={() => select(text(child.id))}>{text(child.id)} · {quantity(liveCells(child))} 活细胞</button>) : <span>子孔未知</span>}</li>)}</ul> : <p>尚无已知谱系</p>}
    <h3>器皿内容</h3><label>选择器皿 <select value={text(vessel?.id)} onChange={event => select(event.target.value)}>{vessels.map(row => <option key={text(row.id)} value={text(row.id)}>{text(row.id)}</option>)}</select></label>
    {vessel ? <dl className="passage-summary"><div><dt>液量 / 容量</dt><dd>{quantity(vessel.volumeUl)} / {quantity(vessel.capacityUl)} µL</dd></div><div><dt>培养物 / 母来源</dt><dd>{text(vessel.culture)} / {vessel.parent === '' ? '无母来源' : text(vessel.parent)}</dd></div><div><dt>活细胞（模型）</dt><dd>{quantity(liveCells(vessel))}</dd></div><div><dt>组分</dt><dd>{rows(vessel.components).map(row => `${text(row.name)} ${quantity(row.volumeUl)} µL`).join('；') || '无已记录组分'}</dd></div></dl> : <p>器皿内容未知</p>}
    {vessel && <table aria-label="所选器皿细胞分布"><thead><tr><th>来源培养物</th><th>分布</th><th>活细胞</th><th>死细胞</th></tr></thead><tbody>{rows(vessel.cells).flatMap(cell => (['attached', 'suspended', 'pellet'] as const).map(part => <tr key={`${cell.culture}:${part}`}><td>{text(cell.culture)}</td><td>{{ attached: '附着', suspended: '悬浮', pellet: '沉淀' }[part]}</td><td>{quantity(cell[`${part}Live`])}</td><td>{quantity(cell[`${part}Dead`])}</td></tr>))}</tbody></table>}
    <h3>试剂、配平与废物</h3><ul>{vessels.filter(row => row.waste === true || (!row.culture && !row.parent)).map(row => <li key={text(row.id)}>{text(row.id)}：{quantity(row.volumeUl)} / {quantity(row.capacityUl)} µL</li>)}</ul>
    <h3>当前占用</h3>{known ? <ul>{rows(snapshot?.reservations).map(row => <li key={`${row.resource}:${row.device}`}>{text(row.resource)} → {text(row.device)}</li>)}</ul> : <p>占用未知</p>}
    <h3>近期物料操作</h3><p>累计 {quantity(snapshot?.operationCount ?? values.operationCount)} · 已归档 {quantity(snapshot?.archivedOperationCount ?? values.archivedOperationCount)}；下列是近期记录及保留的部分操作</p><ul>{rows(snapshot?.operations).slice(-6).reverse().map(row => <li key={text(row.operationId)}>{text(row.action)} · {text(row.vessel)} · {text(row.status)} · 解析 {quantity(row.resolvedUl)} µL / 实转 {quantity(row.appliedUl)} µL / 剩余 {quantity(row.remainingUl)} µL{row.fault ? ` · 故障 ${text(row.fault)}` : ''}</li>)}</ul>
    <h3>最近观察与计数</h3>{observations.length ? <ul>{observations.map(row => <li key={text(row.operationId)}>{text(row.vessel)} · {text(row.kind)} · 测量活/死 {quantity(row.measuredLive)} / {quantity(row.measuredDead)} · 模型活/死 {quantity(row.modelLive)} / {quantity(row.modelDead)} · 物料修订 {quantity(row.materialRevision)} · 来源：{text(row.source)}</li>)}</ul> : <p>尚无已记录观察；不推测计数</p>}
  </section>;
}
