import { useEffect, useState } from 'react';
import { getPlatform, type PlatformSnapshot } from './platform';

export function usePlatform() {
  const [snapshot, setSnapshot] = useState<PlatformSnapshot>();
  const [error, setError] = useState<string>();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    let disposed = false;
    let pending: AbortController | undefined;
    const refresh = async () => {
      setNow(Date.now());
      if (pending) return;
      const controller = new AbortController();
      pending = controller;
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const value = await getPlatform(controller.signal);
        if (!disposed) { setSnapshot(value); setError(undefined); setNow(Date.now()); }
      } catch (error) {
        if (!disposed) setError(controller.signal.aborted ? 'The platform request timed out. Retrying automatically.' : (error as Error).message);
      } finally {
        clearTimeout(timeout);
        pending = undefined;
      }
    };
    void refresh();
    const interval = setInterval(() => void refresh(), 2000);
    return () => { disposed = true; clearInterval(interval); pending?.abort(); };
  }, []);
  return { snapshot, error, now };
}

