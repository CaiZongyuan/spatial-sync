import type { Catalog, JsonValue } from '../protocol';
import { object, passageContract, rows } from './model';

export interface ConfigurationInitial { basis: 'configuration-expectation'; sceneRevision: string; fingerprint: string; material: Record<string, JsonValue> }
const fields = ['attachedLive', 'attachedDead', 'suspendedLive', 'suspendedDead', 'pelletLive', 'pelletDead'];
const fail = (): never => { throw new Error('固定配方配置未知或不合法，不能构造初态预期'); };
function amount(value: unknown, fallback = 0): number {
  const selected = value === undefined ? fallback : value;
  if (typeof selected !== 'number' || !Number.isFinite(selected) || selected < 0) return fail();
  return selected;
}
const text = (value: unknown, fallback = ''): string => value === undefined ? fallback : typeof value === 'string' ? value : fail();
const flag = (value: unknown): boolean => value === undefined ? false : typeof value === 'boolean' ? value : fail();
function array(value: unknown) { if (!Array.isArray(value) || value.some(row => !object(row))) return fail(); return rows(value); }

/** A read-only recipe expectation from the declared v1 model; never a fabricated source snapshot. */
export function configurationInitial(catalog: Catalog, workcell: string, sceneRevision: string): ConfigurationInitial {
  const binding = catalog.entities[workcell];
  if (catalog.sceneRevision !== sceneRevision || binding?.type !== passageContract.workcellType) throw new Error('配置初态的 Catalog/场景/type 不匹配');
  const vessels = array(binding.config.vessels).map(row => {
    const id = text(row.id), entityKey = text(row.entityKey), site = text(row.site), capacityUl = amount(row.capacityUl), volumeUl = amount(row.volumeUl);
    const culture = text(row.culture), parent = text(row.parent), live = amount(row.live), dead = amount(row.dead), attached = flag(row.attached);
    if (!id || !entityKey || !site || !capacityUl || volumeUl > capacityUl || ((live || dead) && !culture)) return fail();
    const cell: Record<string, JsonValue> = { culture, parent, attachedLive: attached ? live : 0, attachedDead: attached ? dead : 0,
      suspendedLive: attached ? 0 : live, suspendedDead: attached ? 0 : dead, pelletLive: 0, pelletDead: 0 };
    return { id, entityKey, capacityUl, volumeUl, culture, parent, waste: flag(row.waste), mixed: true, exposureSince: -1, processedCycle: '',
      components: volumeUl ? [{ name: text(row.reagent) || 'medium', volumeUl }] : [], cells: live || dead ? [cell] : [] };
  });
  const tips = array(binding.config.tips).map(row => {
    if (!text(row.id) || !text(row.site) || !text(row.bodyKey)) return fail();
    return { id: text(row.id), state: 'unused' };
  });
  if (new Set(vessels.map(row => row.id)).size !== vessels.length || new Set(tips.map(row => row.id)).size !== tips.length) return fail();
  const material = { reconciled: true, vessels, tips, tip: { id: '', volumeUl: 0, components: [], cells: [] }, reservations: [], operations: [] };
  return { basis: 'configuration-expectation', sceneRevision, fingerprint: JSON.stringify(material), material };
}

/** Fresh live observations can confirm the configuration; mid-run stock is not an initial state. */
export function compareFreshInitial(expected: ConfigurationInitial, observed: unknown): string[] {
  try {
    const snapshot = object(observed); if (!snapshot) return ['fresh 初态快照未知'];
    const actual = array(snapshot.vessels), errors: string[] = [];
    if (actual.length !== rows(expected.material.vessels).length || new Set(actual.map(row => row.id)).size !== actual.length) errors.push('fresh 器皿清单与配置不同');
    const tolerance = passageContract.quantityTolerances;
    for (const desired of rows(expected.material.vessels)) {
      const row = actual.find(row => row.id === desired.id);
      if (!row || row.culture !== desired.culture || row.parent !== desired.parent || Math.abs(amount(row.volumeUl, NaN) - Number(desired.volumeUl)) > tolerance.finalVolumeUl) { errors.push(`fresh 初始内容不符：${desired.id}`); continue; }
      const components = array(row.components), wanted = rows(desired.components);
      for (const name of new Set([...components, ...wanted].map(value => value.name))) {
        const total = (values: typeof components) => values.filter(value => value.name === name).reduce((sum, value) => sum + amount(value.volumeUl, NaN), 0);
        if (Math.abs(total(components) - total(wanted)) > tolerance.finalVolumeUl) errors.push(`fresh 组分不符：${desired.id}`);
      }
      const cells = array(row.cells), targets = rows(desired.cells);
      for (const culture of new Set([...cells, ...targets].map(value => value.culture))) for (const field of fields) {
        const total = (values: typeof cells) => values.filter(value => value.culture === culture).reduce((sum, value) => sum + amount(value[field], NaN), 0);
        if (Math.abs(total(cells) - total(targets)) > tolerance.cellConservation) errors.push(`fresh 细胞分布不符：${desired.id}`);
      }
    }
    const tip = object(snapshot.tip);
    if (!tip || tip.id || amount(tip.volumeUl, NaN) > tolerance.finalVolumeUl) errors.push('fresh 初态已有持有吸头或在途液体');
    const tips = array(snapshot.tips), desiredTips = rows(expected.material.tips);
    if (tips.length !== desiredTips.length || tips.some(row => row.state !== 'unused' || !desiredTips.some(tip => tip.id === row.id))) errors.push('fresh 吸头清单与配置不同');
    return errors;
  } catch { return ['fresh 初态缺少可核对字段']; }
}
