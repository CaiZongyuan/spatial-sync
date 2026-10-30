import { describe, expect, it } from 'vitest';
import type { PlanDocument, PlanStep } from '../plans/model';
import { liquidAction, literalRemainder, operationReceipt, revisePlans } from './recovery';

function plan(steps: unknown[], id = 'p') { return { id, title: 'test', target: 'session', sessionId: 's', revision: 1, stepResults: {}, steps } as PlanDocument; }
const call = (id: number, action: string, params = {}, status = 'pending') => ({ id, status, do: { entity: '/w', action, params: { owner: 'old', epoch: 0, operationId: `op-${id}`, ...params } } });
describe('reviewed material recovery and stable round identities', () => {
  it('classifies every quantity intent as liquid work', () => {
    for (const name of ['aspirateAvailable', 'aspirateFraction', 'aspirateTopUp']) expect(liquidAction(name)).toBe('aspirate');
    expect(liquidAction('dispenseAll')).toBe('dispense');
  });
  it('shows the original resolved fraction remainder and replaces it with literal work, never 460 * .5', () => {
    const failed = call(1, 'aspirateFraction', { vessel: 'source', fraction: .5 }, 'failed') as PlanStep;
    const receipt = operationReceipt(failed, { operations: [{ operationId: 'op-1', requestedUl: 250, resolvedUl: 250, appliedUl: 40, remainingUl: 210 }] });
    expect(receipt?.remainingUl).toBe(210);
    const replacement = literalRemainder(failed, Number(receipt?.remainingUl));
    expect('do' in replacement && replacement.do).toEqual({ entity: '/w', action: 'aspirate', params: { vessel: 'source', volumeUl: 210 } });
    expect(() => revisePlans([plan([failed])], 'new', 2, { p: { 1: failed } })).toThrow('不能再次计算');
  });
  it('converts dispenseAll explicitly and requires direction for partial mix', () => {
    const failed = call(1, 'dispenseAll', { vessel: 'daughter' }, 'failed') as PlanStep;
    expect('do' in literalRemainder(failed, 60) && (literalRemainder(failed, 60) as typeof failed & { do: unknown }).do).toMatchObject({ action: 'dispense', params: { volumeUl: 60 } });
    expect(() => literalRemainder(call(2, 'mix') as PlanStep, 20)).toThrow('选择吸液或分液');
  });
  it('preserves unstarted IDs and cycle references on owner-only rebind', () => {
    const plans = [plan([call(1, 'start')]), plan([call(2, 'pellet', { cycle: 'op-1' })], 'q')];
    const revised = revisePlans(plans, 'web', 5);
    expect(revised.get('p')?.steps[0]).toMatchObject({ do: { params: { owner: 'web', epoch: 5, operationId: 'op-1' } } });
    expect(revised.get('q')?.steps[0]).toMatchObject({ do: { params: { cycle: 'op-1', operationId: 'op-2' } } });
  });
  it('rewrites known cross-part references only for explicit replacements, preserving done evidence', () => {
    const p = plan([call(1, 'start', {}, 'failed')]);
    const done = call(3, 'pellet', { cycle: 'op-1' }, 'done');
    const q = plan([done, call(2, 'pellet', { cycle: 'op-1' }), { id: 4, status: 'pending', check: { property: 'completedCycle', equals: 'op-1' } }], 'q');
    const revised = revisePlans([p, q], 'web', 5, { p: { 1: call(1, 'start') as PlanStep } }, () => 'replacement-cycle');
    expect(revised.get('p')?.steps[0]).toMatchObject({ do: { params: { operationId: 'replacement-cycle' } } });
    expect(revised.get('q')?.steps[0]).toEqual({ ...done, status: 'pending' });
    expect(revised.get('q')?.steps[1]).toMatchObject({ do: { params: { cycle: 'replacement-cycle' } } });
    expect(revised.get('q')?.steps[2]).toMatchObject({ check: { equals: 'replacement-cycle' } });
  });
  it('rejects pending steps with a previous admitted invocation unless explicitly revised', () => {
    const p = plan([call(1, 'aspirate')]); p.stepResults['1'] = { invocationId: 'previous' };
    expect(() => revisePlans([p], 'web', 2)).toThrow('只观察原调用');
    expect(() => revisePlans([p], 'web', 2, { p: { 1: call(1, 'aspirate') as PlanStep } })).toThrow('只观察原调用');
  });
  it('requires explicit skipped replacements and allows reviewed checks without replaying the action', () => {
    const p = plan([call(1, 'aspirateFraction', { fraction: .5 }, 'skipped')]);
    expect(() => revisePlans([p], 'web', 2)).toThrow('跳过');
    const documents = revisePlans([p], 'web', 2, { p: { 1: { id: 1, status: 'pending', check: { entity: '/w', property: 'controlSafe', equals: true } } } });
    expect(documents.get('p')?.steps[0]).toMatchObject({ check: { property: 'controlSafe' } });
  });
});
