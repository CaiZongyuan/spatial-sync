import { describe, expect, it } from 'vitest';
import type { EntityValues, SceneManifest, Snapshot } from '../protocol';
import { auditFinal, auditMaterial } from './audit';
import type { CycleEvidence } from './cycles';

// Independent arithmetic/geometry fixtures, not a Newton run or mechanical qualification.
function worked() {
  const cell = (culture: string, live: number) => ({ culture, attachedLive: 0, attachedDead: 0, suspendedLive: live, suspendedDead: 0, pelletLive: 0, pelletDead: 0 });
  const vessel = (id: string, volumeUl: number, culture = '', parent = '', live = 0) => ({ id, volumeUl, capacityUl: 10000, culture, parent,
    components: [{ name: 'medium', volumeUl }], cells: live ? [cell(parent || culture, live)] : [] });
  const initial = { reconciled: true, vessels: [vessel('medium', 6120)], tips: [{ id: 'T1', state: 'unused' }], tip: { id: '', volumeUl: 0, components: [], cells: [] }, reservations: [] as unknown[], operations: [] };
  const final = structuredClone(initial); final.vessels = [vessel('medium', 0)]; final.tips[0].state = 'discarded';
  for (const [index, well] of ['A1', 'A2', 'A3', 'B1', 'B2', 'B3'].entries()) {
    const culture = `M${index + 1}`;
    initial.vessels.push(vessel(`Mother/${well}`, 1000, culture, '', 100)); final.vessels.push(vessel(`Mother/${well}`, 0, culture));
    for (const plate of ['Counting', 'DaughterA', 'DaughterB']) {
      const identity = `${plate}/${well}`;
      initial.vessels.push(vessel(identity, 0, identity, culture)); final.vessels.push(vessel(identity, plate === 'Counting' ? 20 : 1000, identity, culture, plate === 'Counting' ? 2 : 49));
    }
  }
  return { initial, final };
}
describe('final observed material gate', () => {
  it('accepts the independently worked six-to-twelve balance', () => { const f = worked(); expect(auditMaterial(f.initial, f.final)).toEqual([]); });
  it('separates sibling allocation tolerance from strict global conservation', () => {
    const f = worked(); const a = f.final.vessels.find(row => row.id === 'DaughterA/A1')!, b = f.final.vessels.find(row => row.id === 'DaughterB/A1')!;
    a.cells[0].suspendedLive = 49.4; b.cells[0].suspendedLive = 48.6; expect(auditMaterial(f.initial, f.final)).toEqual([]);
    a.cells[0].suspendedLive = 49.6; b.cells[0].suspendedLive = 48.4; expect(auditMaterial(f.initial, f.final)).toContain('子孔分配不等：A1');
    a.cells[0].suspendedLive = 49.0001; b.cells[0].suspendedLive = 49; expect(auditMaterial(f.initial, f.final).some(error => error.includes('全量守恒'))).toBe(true);
  });
  it('rejects wrong origins even when total cells and liquid still balance', () => {
    const f = worked(); const a = f.final.vessels.find(row => row.id === 'DaughterA/A1')!, b = f.final.vessels.find(row => row.id === 'DaughterA/A2')!;
    [a.cells, b.cells] = [b.cells, a.cells]; expect(auditMaterial(f.initial, f.final).some(error => error.includes('混样'))).toBe(true);
    f.final.reservations.push({ resource: 'held' }); expect(auditMaterial(f.initial, f.final).some(error => error.includes('占用'))).toBe(true);
  });
});

function geometric() {
  const f = worked();
  const goals = { Mother: 'WasteMother', Counting: 'WasteCounting', DaughterA: 'IncubatorA', DaughterB: 'IncubatorB',
    S1: 'RackS1', S2: 'RackS2', S3: 'RackS3', S4: 'RackS4', S5: 'RackS5', S6: 'RackS6', B5: 'RackB5', B6: 'RackB6' };
  const bodies = Object.fromEntries(Object.keys(goals).map(name => [name, `/body/${name}`]));
  const names = [...Object.values(bodies), '/waste', '/tip'];
  const manifest = { sessionId: 's', sceneRevision: 'r', source: { kind: 'newton' }, nodes: names.map((entityKey, streamId) => ({ kind: 'body', entityKey, streamId })),
    sites: Object.values(goals).map(name => ({ entityKey: `/World/Lab/Sites/${name}`, parentBodyKey: null, localPose: { p: [0, 0, 0], q: [0, 0, 0, 1] } })) } as unknown as SceneManifest;
  const before = { sessionId: 's', sceneRevision: 'r', epoch: 0, simTime: 0, seq: 1, poses: names.map((_, index) => [index, 0, 0, 0, 0, 0, 0, 1]) } as Snapshot;
  const after = structuredClone(before); after.simTime = 1; after.seq = 2;
  const entities: Record<string, EntityValues> = {};
  const put = (key: string, values: unknown) => { entities[key] = { key, values, meta: {} } as EntityValues; };
  for (const [name, site] of Object.entries(goals)) put(bodies[name], { station: `/World/Lab/Sites/${site}`, settled: true, lidClosed: true });
  put('/inc', { environmentReady: true, doorKnown: true, doorOpen: false, signalSource: 'modeled', temperatureC: 37, co2Percent: 5, humidityPercent: 95 });
  put('/cab', { cabinetReady: true, signalSource: 'modeled', inflowMs: .8, sashHeightCm: 20 });
  put('/World/Lab/Robot/fr3_link0', { holding: null, controlSafe: true });
  put('/World/Lab/Centrifuge', { phase: 'idle', controlSafe: true, lidOpen: false, loadedEntities: [null, null, null, null] });
  const cycles: CycleEvidence = { status: 'cycle_evidence_valid', batches: [3, 4].map(stage => ({ stage, status: 'cycle_receipt_valid', errors: [], stageSnapshotAvailable: true })) };
  const layout = { bodies, lids: {}, tips: [{ id: 'T1', bodyKey: '/tip' }], tipGeometry: { lowerLocal: [-.01, -.01, -.01], upperLocal: [.01, .01, .01] },
    solidWasteRegion: { bodyKey: '/waste', lowerLocal: [-.1, -.1, -.1], upperLocal: [.1, .1, .1], contactAllowanceM: 0 }, incubatorEntity: '/inc', cabinetEntity: '/cab',
    environmentLimits: { temperatureC: [36.5, 37.5], co2Percent: [4.5, 5.5], humidityPercent: [90, 100], inflowMs: [.4, 2], sashHeightCm: [18, 22] } };
  return { ...f, bodies, manifest, before, after, entities, layout, cycles };
}
const audit = (f: ReturnType<typeof geometric>) => auditFinal(f.initial, f.final, f.layout, f.manifest, f.before, f.after, f.entities, f.cycles);
describe('end-observation gate remains independent of Plan completion', () => {
  it('checks poses, cells, cycles and teaching environmental ranges but never grants experiment qualification', () => {
    const result = audit(geometric()); expect(result.errors).toEqual([]); expect(result.status).toBe('observations_valid'); expect(result.experimentQualified).toBe(false);
  });
  it('rejects incorrect actual pose despite a matching station label and rejects a discarded tip protruding from waste', () => {
    const f = geometric(); f.after.poses[0][1] = .1;
    expect(audit(f).errors.some(error => error.includes('未实际就座'))).toBe(true);
    f.after.poses[0][1] = 0; f.after.poses.at(-1)![1] = .2;
    expect(audit(f).errors.some(error => error.includes('废吸头超出'))).toBe(true);
  });
  it('does not substitute old RH60 or trust Ready when the published teaching range is violated', () => {
    const f = geometric(); f.entities['/inc'].values.humidityPercent = 60;
    expect(audit(f).environment).toBe(false); expect(audit(f).errors).toContain('教学环境超出固定范围：humidityPercent');
    f.entities['/inc'].values.humidityPercent = 95; f.manifest.source.kind = 'synthetic'; expect(audit(f).spatial).toBe(false);
  });
  it('does not grant end observations from a completed Plan without both cycle receipts', () => {
    const f = geometric(); f.cycles = { status: 'unavailable', batches: [] };
    expect(audit(f).status).toBe('unavailable'); expect(audit(f).cycles).toBe(false);
  });
});
