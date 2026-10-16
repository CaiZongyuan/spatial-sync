import { useCallback, useSyncExternalStore } from 'react';
import type { InfoStore } from './store';

export function useEntity(store: InfoStore, key: string) {
  return useSyncExternalStore(
    useCallback(
      (listener) => store.subscribeEntity(key, listener),
      [store, key],
    ),
    useCallback(() => store.getEntity(key), [store, key]),
  );
}
export function useInvocations(store: InfoStore, key = '') {
  return useSyncExternalStore(
    useCallback(
      (listener) => store.subscribeInvocations(key, listener),
      [store, key],
    ),
    useCallback(() => store.getInvocations(key), [store, key]),
  );
}
