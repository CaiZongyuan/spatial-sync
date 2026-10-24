import { afterEach, expect, it, vi } from 'vitest';
import { agentScore, compareEvaluations, currentReference, readEvaluationCenter, scoreGroups, successRate, suitePath, type EvaluationScore } from './evaluations';

afterEach(() => vi.unstubAllGlobals());

const score = (id: string, conditionsGroup?: string, decision: EvaluationScore['decision'] = 'baseline') => ({
  evaluationId: id, suite: 'suite/real@1', subject: 'robot_arm.virtual', version: 'runtime', key: { source: 'synthetic' },
  conditionsGroup, split: 'train', score: .5, decision, reference: null, successes: 1, episodes: 2,
  ci95: [.09, .91], durationSeconds: 5, recordings: [], phases: {}, at: '2026-10-23T00:00:00Z',
} satisfies EvaluationScore);

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
  expect(currentReference([score('external-result', 'first', 'reported')])).toBeUndefined();
  expect(agentScore(82.5)).toBe('82.5 / 100');
  expect(successRate(.825)).toBe('82.5%');
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
  expect(calls.slice(0, 3).every(value => value.signal === calls[0].signal)).toBe(true);
  expect(calls[3].signal).toBe(controller.signal);
  expect(() => suitePath('suite/../../private@1')).toThrow('无效的评估集版本');
});

it('lists pending and failed evaluations without inventing scores or fetching extra scoreboards', async () => {
  const row = score('completed', 'completed');
  const later = { ...score('later', 'completed', 'accepted'), at: '2026-10-23T01:00:00Z' };
  const fetch = vi.fn(async (path: string) => new Response(JSON.stringify(path.endsWith('/suites')
    ? { suites: [{ id: 'suite/real@1', layer: 'embodied' }] }
    : { evaluations: [{ evaluationId: 'later', status: 'completed', result: later }, { evaluationId: 'queued', status: 'queued' },
      { evaluationId: 'failed', status: 'failed', error: { code: 'source_unavailable' } },
      { evaluationId: 'completed', status: 'completed', result: row }] })));
  vi.stubGlobal('fetch', fetch);
  const data = await readEvaluationCenter(undefined, new AbortController().signal);
  expect(data.rows).toEqual([row, later]);
  expect(currentReference(data.rows)).toEqual(later);
  expect(data.evaluations.map(value => value.evaluationId)).toEqual(['later', 'queued', 'failed', 'completed']);
  expect(fetch.mock.calls.map(([path]) => path)).toEqual(['/api/v1/suites', '/api/v1/evaluations']);
});

it('keeps a partial API failure visible instead of turning it into an empty scoreboard', async () => {
  vi.stubGlobal('fetch', vi.fn(async (path: string) => path.endsWith('/scoreboard')
    ? new Response(JSON.stringify({ detail: { code: 'suite_changed' } }), { status: 409 })
    : new Response(JSON.stringify(path.endsWith('/evaluations') ? { evaluations: [] } : { id: 'suite/real@1' }))));
  await expect(readEvaluationCenter('suite/real@1', new AbortController().signal)).rejects.toMatchObject({
    message: 'suite_changed', detail: { code: 'suite_changed' },
  });
});

it('cancels deferred peers after partial failure before retry, and forwards owner cleanup', async () => {
  const active = new Set<AbortSignal>();
  const peers: AbortSignal[] = [];
  let fail = true;
  vi.stubGlobal('fetch', vi.fn((path: string, options: RequestInit) => {
    if (fail && path.endsWith('/suites')) return Promise.resolve(new Response(
      JSON.stringify({ detail: { code: 'suite_unavailable' } }), { status: 503 }));
    const signal = options.signal!;
    active.add(signal); peers.push(signal);
    return new Promise<Response>((_resolve, reject) => signal.addEventListener('abort', () => {
      active.delete(signal); reject(signal.reason);
    }, { once: true }));
  }));
  const firstOwner = new AbortController();
  await expect(readEvaluationCenter(undefined, firstOwner.signal)).rejects.toMatchObject({
    message: 'suite_unavailable', detail: { code: 'suite_unavailable' },
  });
  expect(peers[0].aborted).toBe(true);
  expect(active.size).toBe(0);
  expect(firstOwner.signal.aborted).toBe(false);
  fail = false;
  const nextOwner = new AbortController();
  const next = readEvaluationCenter(undefined, nextOwner.signal);
  expect(peers.slice(1)).toHaveLength(2);
  expect(peers.slice(1).every(signal => !signal.aborted)).toBe(true);
  nextOwner.abort(new DOMException('Owner left the page', 'AbortError'));
  await expect(next).rejects.toMatchObject({ name: 'AbortError', message: 'Owner left the page' });
  expect(peers.every(signal => signal.aborted)).toBe(true);
  expect(active.size).toBe(0);
  const deadlineOwner = new AbortController();
  const expired = readEvaluationCenter(undefined, deadlineOwner.signal);
  deadlineOwner.abort(new DOMException('Deadline elapsed', 'TimeoutError'));
  await expect(expired).rejects.toMatchObject({ name: 'TimeoutError', message: 'Deadline elapsed' });
  expect(active.size).toBe(0);
});
