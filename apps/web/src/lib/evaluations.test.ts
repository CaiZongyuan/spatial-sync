import { afterEach, expect, it, vi } from 'vitest';
import { compareEvaluations, currentReference, readEvaluationCenter, scoreGroups, suitePath, type EvaluationScore } from './evaluations';

afterEach(() => vi.unstubAllGlobals());

const score = (id: string, conditionsGroup?: string, decision: EvaluationScore['decision'] = 'baseline') => ({
  evaluationId: id, suite: 'suite/real@1', subject: 'robot_arm.virtual', version: 'runtime', key: { source: 'synthetic' },
  conditionsGroup, split: 'train', score: .5, decision,
} as EvaluationScore);

it('keeps complete server condition groups and subjects separate even when public tags are identical', () => {
  const original = score('first', 'first');
  const accepted = score('second', 'first', 'accepted');
  const rejected = score('third', 'first', 'rejected');
  const different = score('private-difference', 'private-difference');
  const anotherSubject = { ...score('other-subject', 'first'), subject: 'another-program' };
  const unclassified = score('older-gateway');
  const groups = scoreGroups([original, different, accepted, anotherSubject, rejected, unclassified, score('unknown')]);
  expect(groups.map(group => group.map(row => row.evaluationId))).toEqual([
    ['first', 'second', 'third'], ['private-difference'], ['other-subject'], ['older-gateway'], ['unknown'],
  ]);
  expect(currentReference(groups[0])).toBe(accepted);
  expect(currentReference([rejected])).toBeUndefined();
});

it('uses canonical Suite REST paths, public reads and the caller cancellation signal', async () => {
  const controller = new AbortController();
  const calls: { path: string; signal: AbortSignal | null | undefined }[] = [];
  const row = score('one', 'one');
  vi.stubGlobal('fetch', vi.fn(async (path: string, options: RequestInit) => {
    calls.push({ path, signal: options.signal });
    const body = path.includes('/scoreboard') ? { rows: [row], comparison: { scoreDelta: .25 } }
      : path.endsWith('/evaluations') ? { evaluations: [{ suite: 'suite/real@1', evaluationId: 'one', result: row }, { suite: 'suite/other@1' }] }
        : { id: 'suite/real@1', layer: 'embodied' };
    return new Response(JSON.stringify(body));
  }));
  const data = await readEvaluationCenter('suite/real@1', controller.signal);
  expect(data.suites[0].id).toBe('suite/real@1');
  expect(data.rows).toEqual([row]);
  expect(data.evaluations.map(value => value.evaluationId)).toEqual(['one']);
  expect((await compareEvaluations('suite/real@1', 'one', 'two', controller.signal)).comparison.scoreDelta).toBe(.25);
  expect(calls.map(value => value.path)).toEqual(['/api/v1/suites/real/1', '/api/v1/evaluations',
    '/api/v1/suites/real/1/scoreboard', '/api/v1/suites/real/1/scoreboard?compare=one&against=two']);
  expect(calls.every(value => value.signal === controller.signal)).toBe(true);
  expect(() => suitePath('suite/../../private@1')).toThrow('无效的评估集版本');
});
