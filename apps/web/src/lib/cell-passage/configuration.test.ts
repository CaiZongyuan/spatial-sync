import { describe, expect, it } from 'vitest';
import type { Catalog } from '../protocol';
import { compareFreshInitial, configurationInitial } from './configuration';
import { object, rows } from './model';
import { auditConservation } from './audit';
const catalog = (): Catalog => ({ sceneRevision: 'r', entities: { '/w': { type: 'cell-passage-workcell/v1', config: {
  vessels: [{ id: 'Mother/A1', entityKey: '/mother', site: '/mother/A1', capacityUl: 1000, volumeUl: 500, culture: 'M1', live: 100, dead: 5, attached: true }], tips: [{ id: 'T1', site: '/tipSite', bodyKey: '/tip' }],
} } }, types: {} }) as unknown as Catalog;
describe('configuration expectation is distinct from an observed initial sample', () => {
  it('retains fixed expected quantities when first reading after an external transfer', () => {
    const expected = configurationInitial(catalog(), '/w', 'r'); const midRun = structuredClone(expected.material);
    rows(midRun.vessels)[0].volumeUl = 460; rows(rows(midRun.vessels)[0].components)[0].volumeUl = 460;
    object(midRun.tip)!.id = 'T1'; object(midRun.tip)!.volumeUl = 40; object(midRun.tip)!.components = [{ name: 'medium', volumeUl: 40 }]; rows(midRun.tips)[0].state = 'installed';
    expect(rows(expected.material.vessels)[0].volumeUl).toBe(500);
    expect(expected.basis).toBe('configuration-expectation'); expect(rows(midRun.vessels)[0].volumeUl).toBe(460);
    expect(auditConservation(expected.material, midRun)).toEqual([]);
    expect(compareFreshInitial(expected, midRun)).toContain('fresh 初始内容不符：Mother/A1');
  });
  it('models known v1 attached/suspended cells without creating or moving material', () => {
    const c = catalog(); const expected = configurationInitial(c, '/w', 'r');
    expect(rows(rows(expected.material.vessels)[0].cells)[0]).toMatchObject({ attachedLive: 100, attachedDead: 5, suspendedLive: 0, pelletLive: 0 });
    expect(compareFreshInitial(expected, expected.material)).toEqual([]);
    expect(c.entities['/w'].config.vessels).toEqual(catalog().entities['/w'].config.vessels);
  });
  it('does not conceal missing stock by treating a short fresh sample as the baseline', () => {
    const expected = configurationInitial(catalog(), '/w', 'r'); const short = structuredClone(expected.material);
    rows(short.vessels)[0].volumeUl = 450; rows(rows(short.vessels)[0].components)[0].volumeUl = 450;
    expect(compareFreshInitial(expected, short)).not.toEqual([]); expect(rows(expected.material.vessels)[0].volumeUl).toBe(500);
    expect(auditConservation(expected.material, short)).toContain('全量守恒失败：components medium');
  });
  it('rejects old catalogs, changed scene fences, unknown types and unknown quantity configuration', () => {
    expect(() => configurationInitial(catalog(), '/w', 'new')).toThrow('不匹配');
    const c = catalog(); c.entities['/w'].type = 'cell-passage-workcell/v2'; expect(() => configurationInitial(c, '/w', 'r')).toThrow('不匹配');
    c.entities['/w'].type = 'cell-passage-workcell/v1'; c.entities['/w'].config.vessels = [{ id: 'unknown' }];
    expect(() => configurationInitial(c, '/w', 'r')).toThrow('未知');
  });
});
