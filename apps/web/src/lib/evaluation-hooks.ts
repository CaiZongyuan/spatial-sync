import { useEffect, useState } from 'react';
import { readEvaluationCenter } from './evaluations';

export function useEvaluationCenter(suiteId?: string) {
  const [attempt, setAttempt] = useState(0);
  const identity = suiteId ?? 'all';
  const [state, setState] = useState<{ identity: string; data?: Awaited<ReturnType<typeof readEvaluationCenter>>; error?: string }>();
  useEffect(() => {
    let disposed = false, pending: AbortController | undefined;
    const read = async () => {
      if (pending) return;
      const controller = new AbortController(); pending = controller;
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const data = await readEvaluationCenter(suiteId, controller.signal);
        if (!disposed && !controller.signal.aborted) setState({ identity, data });
      } catch (error) {
        if (!disposed) setState(previous => ({ ...(previous?.identity === identity ? previous : {}), identity,
          error: controller.signal.aborted ? '读取评估超时，请重试' : (error as Error).message }));
      } finally { clearTimeout(timeout); pending = undefined; }
    };
    void read();
    const timer = setInterval(() => void read(), 5000);
    return () => { disposed = true; clearInterval(timer); pending?.abort(); };
  }, [suiteId, identity, attempt]);
  return { ...(state?.identity === identity ? state : {}), retry: () => setAttempt(value => value + 1) };
}
