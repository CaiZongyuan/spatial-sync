import { describe, expect, it } from 'vitest';
import type { PlanDocument } from '../plans/model';
import { passageRound } from './round';
const documents = () => Array.from({ length: 7 }, (_, index) => ({ id: `cp-r-stage${index + 1}-part1-of1`,
  title: `Cell Passage r · ${index + 1}/7 · stage · part 1/1`, target: 'session', sessionId: 's', sceneRevision: 'rev', epoch: 0,
  status: 'approved', steps: [{ id: (index + 1) * 100000 + 1, status: 'pending', check: { entity: '/w', property: 'controlSafe', equals: true } }],
})) as unknown as PlanDocument[];
describe('complete ordered stage parts', () => {
  it('finds the earliest unfinished part, not the selected later stage', () => {
    const plans = documents(); plans[0].status = 'completed'; plans[0].steps[0].status = 'done';
    expect(passageRound(plans[4].id, [...plans].reverse())?.next?.id).toBe(plans[1].id);
  });
  it('refuses missing parts, conflicting totals and changed session identity', () => {
    const plans = documents(); plans[0].id = 'cp-r-stage1-part1-of2';
    expect(() => passageRound(plans[1].id, plans)).toThrow('总数冲突');
    plans[0] = documents()[0]; plans[2].epoch = 2;
    expect(() => passageRound(plans[1].id, plans)).toThrow('身份不一致');
  });
  it('rejects later part execution and completed plans with skipped work', () => {
    const plans = documents(); plans[2].status = 'running';
    expect(() => passageRound(plans[0].id, plans)).toThrow('乱序');
    plans[2].status = 'approved'; plans[0].status = 'completed'; plans[0].steps[0].status = 'skipped';
    expect(() => passageRound(plans[0].id, plans)).toThrow('完成证据');
  });
});
