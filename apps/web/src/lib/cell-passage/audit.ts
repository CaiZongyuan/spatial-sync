import { Quaternion, Vector3 } from 'three';
import type { EntityValues, JsonValue, Pose, SceneManifest, Snapshot } from '../protocol';
import { multiplyPose } from '../scene/pose';
import { object, passageContract, rows } from './model';
import type { CycleEvidence } from './cycles';

export interface FinalAudit { status: 'observations_valid' | 'invalid' | 'unavailable'; material: boolean; spatial: boolean; environment: boolean; cycles: boolean; errors: string[]; experimentQualified: false }
const cellFields = ['attachedLive', 'attachedDead', 'suspendedLive', 'suspendedDead', 'pelletLive', 'pelletDead'];
const liveFields = ['attachedLive', 'suspendedLive', 'pelletLive'];
function number(value: unknown): number { if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('末态数量或位姿未知'); return value; }
function record(value: unknown) { const row = object(value); if (!row) throw new Error('末态结构未知'); return row; }
function list(value: unknown) { if (!Array.isArray(value) || value.some(item => !object(item))) throw new Error('末态列表未知'); return rows(value); }

export function auditConservation(initialValue: unknown, finalValue: unknown) {
  const initial = record(initialValue), final = record(finalValue), errors: string[] = [];
  const tolerance = passageContract.quantityTolerances;
  function totals(snapshot: Record<string, JsonValue>) {
    const components = new Map<string, number>(), cells = new Map<string, number>();
    for (const row of [...list(snapshot.vessels), record(snapshot.tip)]) {
      const volume = number(row.volumeUl);
      if (volume < 0 || volume > (typeof row.capacityUl === 'number' ? row.capacityUl : Infinity) + tolerance.finalVolumeUl) errors.push(`器皿容量异常：${row.id}`);
      let sum = 0;
      for (const component of list(row.components)) {
        const amount = number(component.volumeUl); if (amount < 0) errors.push('组分为负');
        sum += amount; components.set(String(component.name), (components.get(String(component.name)) ?? 0) + amount);
      }
      if (Math.abs(sum - volume) > tolerance.finalVolumeUl) errors.push(`组分与液量不一致：${row.id}`);
      for (const cell of list(row.cells)) for (const key of cellFields) {
        const amount = number(cell[key]); if (amount < 0) errors.push('细胞量为负');
        cells.set(String(cell.culture), (cells.get(String(cell.culture)) ?? 0) + amount);
      }
    }
    return { components, cells };
  }
  const before = totals(initial), after = totals(final);
  const originals = list(initial.vessels), actual = list(final.vessels);
  if (new Set(actual.map(row => row.id)).size !== actual.length || actual.length !== originals.length || originals.some(row => !actual.some(value => value.id === row.id))) errors.push('器皿清单变更或重复');
  for (const expected of originals) {
    const row = actual.find(row => row.id === expected.id);
    if (row && ['entityKey', 'culture', 'parent'].some(key => key in expected && row[key] !== expected[key])) errors.push(`器皿固定身份不符：${expected.id}`);
  }
  const expectedTips = list(initial.tips), actualTips = list(final.tips), installed = record(final.tip);
  if (expectedTips.length !== actualTips.length || new Set(actualTips.map(row => row.id)).size !== actualTips.length || expectedTips.some(row => !actualTips.some(tip => tip.id === row.id))) errors.push('吸头固定清单变更或重复');
  if ((installed.id && !actualTips.some(tip => tip.id === installed.id && tip.state === 'installed')) || (!installed.id && number(installed.volumeUl) > tolerance.finalVolumeUl)) errors.push('吸头身份或在途液体不一致');
  for (const [kind, limit] of [['components', tolerance.finalVolumeUl], ['cells', tolerance.cellConservation]] as const) {
    for (const key of new Set([...before[kind].keys(), ...after[kind].keys()]))
      if (Math.abs((before[kind].get(key) ?? 0) - (after[kind].get(key) ?? 0)) > limit) errors.push(`全量守恒失败：${kind} ${key}`);
  }
  return errors;
}

export function auditMaterial(initialValue: unknown, finalValue: unknown) {
  const initial = record(initialValue), final = record(finalValue), errors = auditConservation(initial, final);
  const tolerance = passageContract.quantityTolerances;
  const vessels = list(final.vessels), original = list(initial.vessels);
  if (final.reconciled !== true || record(final.tip).id || number(record(final.tip).volumeUl) > tolerance.finalVolumeUl || list(final.reservations).length || list(final.operations).some(row => ['running', 'settling'].includes(String(row.status)))) errors.push('仍有未核对物料、吸头、占用或在途操作');
  const mothers = original.filter(row => String(row.id).startsWith('Mother/'));
  const children = new Set<string>();
  if (mothers.length !== 6 || new Set(mothers.map(row => row.culture)).size !== 6) errors.push('六母孔来源不完整');
  const live = (row: Record<string, JsonValue>) => list(row.cells).reduce((sum, cell) => sum + liveFields.reduce((value, key) => value + number(cell[key]), 0), 0);
  for (const mother of mothers) {
    const well = String(mother.id).split('/')[1];
    const sample = vessels.find(row => row.id === `Counting/${well}`);
    const daughters = ['DaughterA', 'DaughterB'].map(name => vessels.find(row => row.id === `${name}/${well}`));
    if (!sample || daughters.some(row => !row)) { errors.push(`孔位去向未知：${well}`); continue; }
    for (const daughter of daughters) {
      if (!daughter || daughter.parent !== mother.culture || !daughter.culture || daughter.culture === mother.culture || children.has(String(daughter.culture))) errors.push(`子培养物身份错误：${well}`);
      else children.add(String(daughter.culture));
      if (!daughter || Math.abs(number(daughter.volumeUl) - 1000) > tolerance.finalVolumeUl || live(daughter) <= 0) errors.push(`子孔产量/液量错误：${well}`);
    }
    for (const row of [sample, ...daughters]) if (row && list(row.cells).some(cell => cell.culture !== mother.culture)) errors.push(`混样：${row.id}`);
    if (Math.abs(number(sample.volumeUl) - 20) > tolerance.finalVolumeUl || live(sample) <= 0) errors.push(`计数取样错误：${well}`);
    if (daughters[0] && daughters[1] && Math.abs(live(daughters[0]) - live(daughters[1])) > tolerance.siblingLive) errors.push(`子孔分配不等：${well}`);
  }
  for (const row of vessels.filter(row => !row.waste)) {
    const start = original.find(value => value.id === row.id);
    const origin = start?.parent || start?.culture;
    if (list(row.cells).some(cell => cell.culture !== origin && cellFields.reduce((sum, key) => sum + number(cell[key]), 0) > tolerance.cellConservation)) errors.push(`残留来源异常：${row.id}`);
  }
  return errors;
}

function poses(manifest: SceneManifest, frame: Snapshot) {
  const values = new Map<string, Pose>();
  for (const node of manifest.nodes) if (node.kind === 'body') {
    const row = frame.poses.find(row => row[0] === node.streamId);
    if (!row || row.length !== 8 || !row.every(Number.isFinite)) throw new Error(`物理位姿未知：${node.entityKey}`);
    const q = new Quaternion(row[4], row[5], row[6], row[7]); if (q.length() < .5) throw new Error('四元数无效');
    values.set(node.entityKey, { p: [row[1], row[2], row[3]], q: q.normalize().toArray() });
  }
  return values;
}

/** Public read models establish end observations, not collision/transport or recording qualification. */
export function auditFinal(initial: unknown, final: unknown, layoutValue: unknown, manifest: SceneManifest, before: Snapshot, after: Snapshot,
  entities: Record<string, EntityValues>, cycles?: CycleEvidence): FinalAudit {
  const cycleValid = cycles?.status === 'cycle_evidence_valid' && cycles.batches.length === 2 && passageContract.centrifugeCycleReceipt.stages.every(stage => cycles.batches.filter(batch => batch.stage === stage && batch.status === 'cycle_receipt_valid').length === 1);
  const result: FinalAudit = { status: 'invalid', material: false, spatial: false, environment: false, cycles: cycleValid, errors: [], experimentQualified: false };
  try {
    result.errors.push(...auditMaterial(initial, final)); result.material = result.errors.length === 0;
    const layout = record(layoutValue), bodies = record(layout.bodies), lids = record(layout.lids);
    if (before.sceneRevision !== after.sceneRevision || before.epoch !== after.epoch || before.sessionId !== after.sessionId ||
        after.sceneRevision !== manifest.sceneRevision || after.simTime - before.simTime < .1) throw new Error('末态需同一会话两个不同仿真时刻的实际空间样本');
    const oldPoses = poses(manifest, before), current = poses(manifest, after), dt = after.simTime - before.simTime;
    const expected = new Map<string, string>();
    for (const [name, site] of Object.entries({ Mother: 'WasteMother', Counting: 'WasteCounting', DaughterA: 'IncubatorA', DaughterB: 'IncubatorB',
      ...Object.fromEntries(Array.from({ length: 6 }, (_, index) => [`S${index + 1}`, `RackS${index + 1}`])), B5: 'RackB5', B6: 'RackB6' })) expected.set(String(bodies[name]), `/World/Lab/Sites/${site}`);
    for (const [body, lid] of Object.entries(lids)) expected.set(String(lid), `${body}/LidSeat`);
    let spatialErrors = 0;
    const sitePose = (key: string, samples: Map<string, Pose>) => {
      const site = manifest.sites.find(row => row.entityKey === key); if (!site) throw new Error(`实际 Site 未知：${key}`);
      if (!site.parentBodyKey) return site.localPose;
      const parent = samples.get(site.parentBodyKey); if (!parent) throw new Error('Site 父体位姿未知'); return multiplyPose(parent, site.localPose);
    };
    const tolerance = passageContract.mechanicalTolerances;
    for (const [body, site] of expected) {
      const actual = current.get(body), previous = oldPoses.get(body), desired = sitePose(site, current), previousDesired = sitePose(site, oldPoses);
      if (!actual || !previous) throw new Error(`器皿物理位姿未知：${body}`);
      const distance = new Vector3(...actual.p).distanceTo(new Vector3(...desired.p));
      const angle = new Quaternion(...actual.q).angleTo(new Quaternion(...desired.q)) * 180 / Math.PI;
      const speed = Math.hypot(...actual.p.map((value, index) => (value - previous.p[index] - desired.p[index] + previousDesired.p[index]) / dt));
      const fact = entities[body]?.values;
      if (distance >= tolerance.positionM || angle > tolerance.orientationDegrees || speed >= tolerance.relativeSpeedMps || fact?.station !== site || fact?.settled !== true) { result.errors.push(`末态未实际就座：${body}`); spatialErrors++; }
    }
    for (const body of Object.values(bodies).filter(key => typeof key === 'string' && entities[key])) if ('lidClosed' in entities[String(body)].values && entities[String(body)].values.lidClosed !== true) { result.errors.push(`器皿盖未关闭：${body}`); spatialErrors++; }
    const material = record(final), tipStates = list(material.tips), configuredTips = list(layout.tips), region = record(layout.solidWasteRegion), geometry = record(layout.tipGeometry);
    const waste = current.get(String(region.bodyKey)); if (!waste || configuredTips.length !== tipStates.length || new Set(tipStates.map(row => row.id)).size !== tipStates.length || configuredTips.some(row => !tipStates.some(tip => tip.id === row.id))) throw new Error('废物或吸头物理清单未知');
    const bounds = (row: Record<string, JsonValue>, key: string) => { const value = row[key]; if (!Array.isArray(value) || value.length !== 3) throw new Error('物理边界未知'); return value.map(number); };
    const lower = bounds(region, 'lowerLocal'), upper = bounds(region, 'upperLocal'), tipLower = bounds(geometry, 'lowerLocal'), tipUpper = bounds(geometry, 'upperLocal');
    const allowance = number(region.contactAllowanceM);
    for (const tip of tipStates) {
      if (tip.state === 'unused') continue;
      const config = configuredTips.find(row => row.id === tip.id), pose = current.get(String(config?.bodyKey));
      if (tip.state !== 'discarded' || !pose) throw new Error('吸头未弃置或实际位姿未知');
      for (let index = 0; index < 8; index++) {
        const point = new Vector3(...tipLower.map((value, axis) => index & 1 << axis ? tipUpper[axis] : value) as [number, number, number])
          .applyQuaternion(new Quaternion(...pose.q)).add(new Vector3(...pose.p)).sub(new Vector3(...waste.p)).applyQuaternion(new Quaternion(...waste.q).invert());
        if (point.toArray().some((value, axis) => value < lower[axis] - allowance || value > upper[axis] + allowance)) { result.errors.push(`废吸头超出实际收集区域：${tip.id}`); spatialErrors++; break; }
      }
    }
    result.spatial = spatialErrors === 0 && manifest.source.kind === 'newton';
    if (manifest.source.kind !== 'newton') result.errors.push('Synthetic 不能作为实际机械末态证据');
    const incubator = entities[String(layout.incubatorEntity)]?.values, cabinet = entities[String(layout.cabinetEntity)]?.values;
    const environmentErrors: string[] = [];
    if (incubator?.environmentReady !== true || incubator.doorKnown !== true || incubator.doorOpen !== false || cabinet?.cabinetReady !== true ||
        incubator?.signalSource !== 'modeled' || cabinet?.signalSource !== 'modeled') environmentErrors.push('实际门状态或教学环境 Ready 未建立');
    for (const [key, range] of Object.entries(record(layout.environmentLimits))) {
      if (!Array.isArray(range) || range.length !== 2) throw new Error('固定教学范围未知');
      const value = ['inflowMs', 'sashHeightCm'].includes(key) ? cabinet?.[key] : incubator?.[key];
      if (typeof value !== 'number' || value < number(range[0]) || value > number(range[1])) environmentErrors.push(`教学环境超出固定范围：${key}`);
    }
    result.errors.push(...environmentErrors); result.environment = environmentErrors.length === 0;
    const robot = entities['/World/Lab/Robot/fr3_link0']?.values, centrifuge = entities['/World/Lab/Centrifuge']?.values;
    if (robot?.holding !== null || robot.controlSafe !== true || centrifuge?.phase !== 'idle' || centrifuge.controlSafe !== true || centrifuge.lidOpen !== false ||
        !Array.isArray(centrifuge.loadedEntities) || centrifuge.loadedEntities.length !== 4 || centrifuge.loadedEntities.some(value => value !== null)) result.errors.push('末态机器人持物或离心机未清空/停稳');
    if (!result.cycles) result.errors.push(...(cycles?.batches.flatMap(batch => batch.errors.map(error => `第 ${batch.stage} 阶段：${error}`)) ?? []), '缺少两个一致的 Plan 绑定实际离心完成 receipt');
    result.status = !cycles || cycles.status === 'unavailable' ? 'unavailable' : result.errors.length ? 'invalid' : 'observations_valid';
  } catch (error) { result.status = 'unavailable'; result.errors.push(error instanceof Error ? error.message : String(error)); }
  return result;
}
