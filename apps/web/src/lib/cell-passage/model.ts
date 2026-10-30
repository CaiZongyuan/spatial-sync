import passageContract from '../../../../../tools/cell_passage_contract.json';
export { passageContract };
import type { EntityValues, JsonValue } from '../protocol';

type Row = Record<string, JsonValue>;
export const object = (value: unknown): Row | undefined => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Row : undefined;
export const rows = (value: unknown): Row[] => Array.isArray(value) ? value.map(object).filter((row): row is Row => !!row) : [];
export const text = (value: unknown) => typeof value === 'string' && value ? value : '未知';
export const quantity = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '未知';
export function materialState(entity: EntityValues | undefined) {
  const snapshot = object(entity?.values.materialSnapshot);
  const vessels = rows(snapshot?.vessels);
  const numeric = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const array = (value: unknown) => Array.isArray(value) && value.every(row => !!object(row));
  const known = !!snapshot && Number.isInteger(snapshot.revision) && array(snapshot.vessels) &&
    vessels.every(row => typeof row.id === 'string' && typeof row.entityKey === 'string' && numeric(row.volumeUl) &&
      numeric(row.capacityUl) && array(row.cells) && array(row.components)) &&
    array(snapshot.reservations) && array(snapshot.observations) && array(snapshot.operations) && array(snapshot.tips) &&
    array(snapshot.controlRequests) && !!object(snapshot.tip) && numeric(object(snapshot.tip)?.volumeUl);
  return { snapshot, vessels, known, reconciled: known && snapshot.reconciled === true,
    stale: entity?.meta.materialSnapshot?.stale === true };
}
export function liveCells(vessel: Row) {
  if (!Array.isArray(vessel.cells)) return undefined;
  const cells = rows(vessel.cells);
  const values = cells.flatMap(row => [row.attachedLive, row.suspendedLive, row.pelletLive]);
  return values.every(value => typeof value === 'number' && Number.isFinite(value)) ?
    values.reduce<number>((sum, value) => sum + Number(value), 0) : undefined;
}
export function lineage(vessels: Row[]) {
  // Culture/parent are authoritative; container names and cell-origin rows are not daughter identities.
  const children = vessels.filter(row => typeof row.culture === 'string' && row.culture && typeof row.parent === 'string' && row.parent);
  return vessels.filter(row => typeof row.culture === 'string' && row.culture && row.parent === '')
    .map(mother => ({ mother, children: children.filter(child => child.parent === mother.culture) }));
}


export function passageStage(stepId: number | undefined) {
  if (!stepId) return '等待计划';
  return passageContract.stages.find(stage => stepId >= stage.firstStep && stepId <= stage.lastStep)?.title ?? '计划步骤';
}


export function factLabel(value: unknown): string {
  if (value === null) return '无';
  if (Array.isArray(value)) return value.map(factLabel).join('、');
  const record = object(value);
  if (record) return Object.entries(record).map(([key, item]) => `${key}: ${factLabel(item)}`).join('；');
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return quantity(value);
  return value === '' ? '空' : text(value);
}
