import { describe, expect, it } from 'vitest';
import { observeTube, type ComparisonReport } from '../../../../tools/agent_journey_checks';

const tubes = [1, 2, 3, 4].map((index) => `/World/Lab/Tubes/Tube0${index}`);

function setup() {
  const report: ComparisonReport = {
    locationComparisons: 0, poseComparisons: 0,
    poseComparisonsByTube: Object.fromEntries(tubes.map((key) => [key, 0])),
    maxPoseDelta: 0, failures: [],
  };
  const strikes = new Map<string, number>();
  const observe = (key = tubes[0], changes = {}) => observeTube(report, strikes, {
    key, simTime: 1, location: 'rack', shown: 'rack', pose: [0, 0, 0], before: [0, 0, 0], rendered: [0, 0, 0], ...changes,
  });
  return { report, observe };
}

describe('I5 sampled browser consistency', () => {
  it('rejects a permanently missing Tube01 while the other three remain correct', () => {
    const { report, observe } = setup();
    for (let sample = 0; sample < 3; sample++)
      for (const key of tubes) observe(key, key === tubes[0] ? { rendered: undefined } : {});
    expect(report.locationComparisons).toBe(12);
    expect(report.poseComparisons).toBe(9);
    expect(report.poseComparisonsByTube).toEqual(Object.fromEntries(tubes.map((key, index) => [key, index ? 3 : 0])));
    expect(report.failures).toEqual([expect.objectContaining({ key: tubes[0], renderedValid: false })]);
  });

  it.each([undefined, null, [], Array(3), [0, 0], [NaN, 0, 0], [Infinity, 0, 0]])('rejects persistently invalid rendered data: %s', (rendered) => {
    const { report, observe } = setup();
    observe(tubes[0], { rendered });
    expect(report.failures).toEqual([]);
    observe(tubes[0], { rendered });
    expect(report.failures).toHaveLength(1);
    expect(report.poseComparisonsByTube[tubes[0]]).toBe(0);
    expect(Number.isFinite(report.maxPoseDelta)).toBe(true);
  });

  it.each([undefined, [NaN, 0, 0]])('rejects missing or non-finite current API poses: %s', (pose) => {
    const { report, observe } = setup();
    observe(tubes[0], { pose });
    observe(tubes[0], { pose });
    expect(report.failures).toHaveLength(1);
    expect(report.poseComparisons).toBe(0);
  });

  it('allows finite motion and the first sample without pretending they are resting comparisons', () => {
    const { report, observe } = setup();
    observe(tubes[0], { before: undefined });
    observe(tubes[0], { pose: [1, 0, 0], rendered: [0.9, 0, 0] });
    observe(tubes[0], { pose: [2, 0, 0], before: [1, 0, 0], rendered: [1.9, 0, 0] });
    expect(report.failures).toEqual([]);
    expect(report.poseComparisons).toBe(0);
    observe();
    expect(report.poseComparisonsByTube[tubes[0]]).toBe(1);
  });

  it('requires two consecutive disagreements and resets after a good sample', () => {
    const { report, observe } = setup();
    observe(tubes[0], { rendered: undefined });
    observe();
    observe(tubes[0], { rendered: [0.1, 0, 0] });
    expect(report.failures).toEqual([]);
    observe(tubes[0], { rendered: [0.1, 0, 0] });
    expect(report.failures).toHaveLength(1);
    expect(report.maxPoseDelta).toBe(0.1);
  });

  it('still rejects an absent tube during finite motion', () => {
    const { report, observe } = setup();
    observe(tubes[0], { pose: [1, 0, 0], rendered: undefined });
    observe(tubes[0], { pose: [2, 0, 0], before: [1, 0, 0], rendered: undefined });
    expect(report.failures).toHaveLength(1);
    expect(report.poseComparisons).toBe(0);
  });

  it('allows quantized resting poses while detecting persistent location disagreement', () => {
    const { report, observe } = setup();
    observe(tubes[0], { rendered: [0.00005, 0, 0] });
    observe(tubes[0], { rendered: [0.00005, 0, 0] });
    expect(report.failures).toEqual([]);
    observe(tubes[0], { shown: 'other' });
    observe(tubes[0], { shown: 'other' });
    expect(report.failures).toHaveLength(1);
  });
});
