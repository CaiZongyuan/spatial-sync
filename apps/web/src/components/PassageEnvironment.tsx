import type { SessionClient } from '../lib/stream/client';
import { useEntity } from '../lib/info/hooks';
import { factLabel, quantity } from '../lib/cell-passage/model';

export function PassageEnvironment({ client, entityKey, type }: { client: SessionClient; entityKey: string; type: string }) {
  const values = useEntity(client.info, entityKey)?.values ?? {};
  const range = (value: unknown) => Array.isArray(value) && value.length === 2 ? `${quantity(value[0])}–${quantity(value[1])}` : '未知';
  const incubator = type === 'passage-incubator/v1';
  return <section className="passage-environment" aria-label={incubator ? '培养教学环境' : '操作柜教学环境'}>
    <h3>{incubator ? '培养箱' : '操作柜'} · {values.signalSource === 'modeled' ? '教学信号模型' : '信号来源未知'}</h3>
    {incubator ? <dl className="passage-summary">
      <div><dt>温度 / 教学范围 °C</dt><dd>{quantity(values.temperatureC)} / {range(values.temperatureReadyRangeC)}</dd></div>
      <div><dt>CO₂ / 教学范围 %</dt><dd>{quantity(values.co2Percent)} / {range(values.co2ReadyRangePercent)}</dd></div>
      <div><dt>RH / 教学范围 %</dt><dd>{quantity(values.humidityPercent)} / {range(values.humidityReadyRangePercent)} · 设定 {quantity(values.humiditySetpointPercent)}</dd></div>
      <div><dt>源侧门状态 / 环境 Ready</dt><dd>{values.doorKnown === true ? values.doorOpen === true ? '门开' : values.doorOpen === false ? '门闭' : '未知' : '门状态未知'} / {factLabel(values.environmentReady)}</dd></div>
    </dl> : <dl className="passage-summary"><div><dt>气流 / 窗高</dt><dd>{quantity(values.inflowMs)} m/s / {quantity(values.sashHeightCm)} cm</dd></div><div><dt>风机 / UV / Ready</dt><dd>{factLabel(values.blowerOn)} / {factLabel(values.uvOn)} / {factLabel(values.cabinetReady)}</dd></div></dl>}
  </section>;
}
