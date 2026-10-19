import { expect, it } from 'vitest';
import { InfoStore } from './store';

it('retains reliable activity once without treating an event as current property authority, and clears it at reset', () => {
  const store = new InfoStore();
  store.configure('scene', 0);
  store.apply({ type: 'entity.snapshot', sceneRevision: 'scene', epoch: 0, simTime: 0, sampleTimeMs: 0,
    entities: [{ key: '/device', rev: 1, values: { phase: 'idle' }, meta: {} }], invocations: [] });
  const event = { type: 'entity.event' as const, sceneRevision: 'scene', epoch: 0, entityKey: '/device', event: 'runCompleted',
    data: { phase: 'running' }, simTime: 1, sampleTimeMs: 1000 };
  store.apply(event); store.apply(event);
  expect(store.getActivity()).toHaveLength(1);
  expect(store.getActivity()[0]).toMatchObject({ label: 'runCompleted', entityKey: '/device', actor: 'system', simTime: 1 });
  expect(store.getEntity('/device')?.values.phase).toBe('idle');
  store.apply({ type: 'entity.snapshot', sceneRevision: 'scene', epoch: 1, simTime: 0, sampleTimeMs: 0, entities: [], invocations: [] });
  expect(store.getActivity()).toEqual([]);
  store.apply(event);
  expect(store.getActivity()).toEqual([]);
  store.dispose();
});
