import { expect, it, vi } from 'vitest';
import { InfoStore } from './store';

it('applies an authoritative snapshot then only newer observations, resetting authority by epoch', () => {
  const store = new InfoStore();
  store.configure('scene-a', 0);
  store.apply({
    type: 'entity.snapshot',
    simTime: 0,
    sampleTimeMs: 0,
    sceneRevision: 'scene-a',
    epoch: 0,
    entities: [
      { key: '/machine', rev: 2, values: { phase: 'idle', rpm: 0 }, meta: {} },
    ],
    invocations: [],
  });
  const original = store.getEntity('/machine');
  store.apply({
    type: 'entity.observations',
    sceneRevision: 'scene-a',
    epoch: 0,
    simTime: 4,
    sampleTimeMs: 400,
    source: 'virtual',
    changes: [{ key: '/machine', rev: 3, values: { rpm: 500 } }],
  });
  expect(store.getEntity('/machine')).toMatchObject({
    rev: 3,
    values: { phase: 'idle', rpm: 500 },
    meta: { rpm: { simTime: 4, sampleTimeMs: 400, source: 'virtual' } },
  });
  expect(original?.values.rpm).toBe(0);
  store.apply({
    type: 'entity.observations',
    sceneRevision: 'scene-a',
    epoch: 0,
    simTime: 3,
    sampleTimeMs: 300,
    source: 'virtual',
    changes: [{ key: '/machine', rev: 2, values: { rpm: 100 } }],
  });
  expect(store.getEntity('/machine')?.values.rpm).toBe(500);
  store.apply({
    type: 'entity.snapshot',
    simTime: 0,
    sampleTimeMs: 0,
    sceneRevision: 'scene-a',
    epoch: 1,
    entities: [
      { key: '/machine', rev: 0, values: { phase: 'idle', rpm: 0 }, meta: {} },
    ],
    invocations: [],
  });
  store.apply({
    type: 'entity.observations',
    sceneRevision: 'scene-a',
    epoch: 0,
    simTime: 9,
    sampleTimeMs: 900,
    source: 'virtual',
    changes: [{ key: '/machine', rev: 9, values: { rpm: 900 } }],
  });
  expect(store.getEntity('/machine')?.values.rpm).toBe(0);
  store.dispose();
});

it('notifies only the changed entity and coalesces tree phase badges to four updates per second', () => {
  vi.useFakeTimers();
  try {
    const store = new InfoStore();
    store.configure('scene', 0);
    store.apply({
      type: 'entity.snapshot',
      simTime: 0,
      sampleTimeMs: 0,
      sceneRevision: 'scene',
      epoch: 0,
      entities: [
        { key: '/selected', rev: 0, values: { phase: 'idle' }, meta: {} },
      ],
      invocations: [],
    });
    const selected = vi.fn();
    const badges = vi.fn();
    store.subscribeEntity('/selected', selected);
    store.subscribeTree(badges);
    for (let rev = 1; rev <= 10; rev++)
      store.apply({
        type: 'entity.observations',
        sceneRevision: 'scene',
        epoch: 0,
        simTime: rev,
        sampleTimeMs: rev,
        source: 'virtual',
        changes: [{ key: '/other', rev, values: { phase: 'moving' } }],
      });
    expect(selected).not.toHaveBeenCalled();
    expect(badges).not.toHaveBeenCalled();
    vi.advanceTimersByTime(250);
    expect(badges).toHaveBeenCalledTimes(1);
    expect(store.getTree()['/other']).toBe('moving');
    store.apply({
      type: 'entity.observations',
      sceneRevision: 'scene',
      epoch: 0,
      simTime: 1,
      sampleTimeMs: 1,
      source: 'virtual',
      changes: [{ key: '/selected', rev: 1, values: { phase: 'running' } }],
    });
    expect(selected).toHaveBeenCalledTimes(1);
    store.dispose();
  } finally {
    vi.useRealTimers();
  }
});

it('keeps invocation lifecycle and actor details available per entity across snapshots', () => {
  const store = new InfoStore();
  store.configure('scene', 0);
  const invocation = {
    invocationId: 'inv-1',
    requestId: 'req-1',
    entityKey: '/machine',
    action: 'start',
    params: { rpm: 500 },
    actor: { kind: 'user' as const, name: 'operator' },
    epoch: 0,
    createdSimTime: 0,
    createdAtMs: 1,
    status: 'running' as const,
    progress: 0.5,
  };
  store.apply({
    type: 'entity.snapshot',
    sceneRevision: 'scene',
    epoch: 0,
    simTime: 0,
    sampleTimeMs: 0,
    entities: [],
    invocations: [invocation],
  });
  expect(store.getInvocations('/machine')).toEqual([invocation]);
  expect(store.getInvocations('/unrelated')).toEqual([]);
  store.apply({
    ...invocation,
    type: 'invocation.update',
    sceneRevision: 'scene',
    status: 'succeeded',
    progress: 1,
    endedSimTime: 10,
  });
  expect(store.getInvocations('/machine')[0]).toMatchObject({
    status: 'succeeded',
    actor: { name: 'operator' },
    endedSimTime: 10,
  });
  store.configure('new-scene', 0);
  store.apply({
    ...invocation,
    type: 'invocation.update',
    sceneRevision: 'scene',
  });
  expect(store.getInvocations()).toEqual([]);
  store.dispose();
});

it('does not refresh an unrelated device invocation subscription', () => {
  const store = new InfoStore();
  store.configure('scene', 0);
  const make = (entityKey: string, id: string) => ({
    invocationId: id,
    requestId: id,
    entityKey,
    action: 'run',
    params: {},
    actor: { kind: 'user' as const, name: 'web' },
    epoch: 0,
    createdSimTime: 0,
    createdAtMs: 1,
    status: 'running' as const,
  });
  const selected = make('/selected', 'inv-a');
  store.apply({
    type: 'entity.snapshot',
    sceneRevision: 'scene',
    epoch: 0,
    simTime: 0,
    sampleTimeMs: 0,
    entities: [],
    invocations: [selected],
  });
  const notify = vi.fn();
  store.subscribeInvocations('/selected', notify);
  const before = store.getInvocations('/selected');
  store.apply({
    ...make('/other', 'inv-b'),
    type: 'invocation.update',
    sceneRevision: 'scene',
  });
  expect(store.getInvocations('/selected')).toBe(before);
  expect(notify).not.toHaveBeenCalled();
  store.dispose();
});

it('merges same-revision property flushes without letting an older property sample overwrite them', () => {
  const store = new InfoStore();
  store.configure('scene', 0);
  store.apply({
    type: 'entity.snapshot',
    sceneRevision: 'scene',
    epoch: 0,
    simTime: 3,
    sampleTimeMs: 300,
    entities: [
      {
        key: '/centrifuge',
        rev: 7,
        values: { phase: 'decelerating', rpm: 83 },
        meta: { rpm: { simTime: 3, sampleTimeMs: 300, source: 'virtual' } },
      },
    ],
    invocations: [],
  });
  store.apply({
    type: 'entity.observations',
    sceneRevision: 'scene',
    epoch: 0,
    simTime: 4,
    sampleTimeMs: 400,
    source: 'virtual',
    changes: [{ key: '/centrifuge', rev: 8, values: { phase: 'idle' } }],
  });
  store.apply({
    type: 'entity.observations',
    sceneRevision: 'scene',
    epoch: 0,
    simTime: 5,
    sampleTimeMs: 500,
    source: 'virtual',
    changes: [{ key: '/centrifuge', rev: 8, values: { rpm: 0 } }],
  });
  expect(store.getEntity('/centrifuge')?.values).toEqual({
    phase: 'idle',
    rpm: 0,
  });
  store.apply({
    type: 'entity.observations',
    sceneRevision: 'scene',
    epoch: 0,
    simTime: 4.5,
    sampleTimeMs: 450,
    source: 'virtual',
    changes: [{ key: '/centrifuge', rev: 8, values: { rpm: 60 } }],
  });
  expect(store.getEntity('/centrifuge')?.values.rpm).toBe(0);
  store.dispose();
});
