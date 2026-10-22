import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { request } from '../api';
import type { OperationPlan } from '../protocol';
import type { SessionClient } from '../stream/client';
import { previewMatches, type MotionPreview, type PlanDocument, type PlanStep, type PreviewScope } from './model';
import type { PlanStore } from './store';

const empty = { entries: [], deviations: [] } satisfies ReturnType<PlanStore['getSnapshot']>;
const noSubscribe = () => () => {};
const emptySnapshot = () => empty;
const noView = () => undefined;
export const useClientView = (client?: SessionClient) =>
  useSyncExternalStore(client?.subscribe ?? noSubscribe, client?.getSnapshot ?? noView);
export const useClientPlans = (client?: SessionClient) =>
  useSyncExternalStore(client?.plans.subscribe ?? noSubscribe, client?.plans.getSnapshot ?? emptySnapshot);

export function usePlansList() {
  const [state, setState] = useState<{ plans?: PlanDocument[]; error?: string }>({});
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let disposed = false, pending: AbortController | undefined;
    const refresh = async () => {
      if (pending) return;
      const controller = new AbortController(); pending = controller;
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const value = await request<{ plans: PlanDocument[] }>('/plans?kind=operation', { signal: controller.signal });
        if (!disposed) setState({ plans: value.plans.sort((a, b) => b.createdAtMs - a.createdAtMs) });
      } catch (error) { if (!disposed) setState(prior => ({ ...prior, error: (error as Error).message })); }
      finally { clearTimeout(timeout); pending = undefined; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 3000);
    return () => { disposed = true; clearInterval(timer); pending?.abort(); };
  }, [retry]);
  return { ...state, retry: () => setRetry(value => value + 1) };
}

export function usePlanDocument(id?: string, revision?: number, enabled = true, refreshKey = '') {
  const identity = `${enabled}:${id ?? ''}:${revision ?? 'latest'}`;
  const [retry, setRetry] = useState(0);
  const refresh = useCallback(() => setRetry(value => value + 1), []);
  const [state, setState] = useState<{ identity?: string; document?: PlanDocument; previous?: PlanDocument; historyError?: string; error?: string; loading?: boolean }>({});
  useEffect(() => {
    if (!enabled || !id) return;
    let disposed = false;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    setState(prior => ({ ...(prior.identity === identity ? prior : {}), identity, loading: true, error: undefined }));
    const read = async () => {
      try {
        const document = await request<PlanDocument>(`/plans/${encodeURIComponent(id)}${revision ? `?revision=${revision}` : ''}`, { signal: controller.signal });
        if (document.id !== id || (revision && document.revision !== revision)) throw new Error('计划修订不一致');
        let previous: PlanDocument | undefined, historyError: string | undefined;
        try {
          if (document.revision > 1) previous = await request<PlanDocument>(
            `/plans/${encodeURIComponent(id)}?revision=${document.revision - 1}`, { signal: controller.signal });
        } catch (error) { if (controller.signal.aborted) throw error; historyError = (error as Error).message; }
        if (!disposed && !controller.signal.aborted) setState({ identity, document, previous, historyError });
      } catch (error) {
        if (!disposed) setState(prior => ({ ...(prior.identity === identity ? prior : {}),
          identity, error: controller.signal.aborted ? '读取计划超时，请重试' : (error as Error).message, loading: false }));
      } finally { clearTimeout(timeout); }
    };
    void read();
    return () => { disposed = true; clearTimeout(timeout); controller.abort(); };
  }, [id, revision, enabled, identity, refreshKey, retry]);
  return { ...(enabled && state.identity === identity ? state : { loading: enabled && Boolean(id) }), refresh };
}

export function useMotionPreview(client: SessionClient | undefined, plan: OperationPlan | undefined,
  document: PlanDocument | undefined, selected: PlanStep | undefined, refreshKey = '') {
  const view = useClientView(client);
  const connected = view?.connection === 'connected' && ['RUNNING', 'PAUSED'].includes(view.sessionState);
  const current = document && document.id === plan?.id && document.revision === plan.revision;
  const sameContext = current && view?.manifest?.sceneRevision === document.sceneRevision
    && client?.buffer.epoch === document.epoch && client.sessionId === document.sessionId;
  const scope: PreviewScope | undefined = current && selected ? {
    planId: document.id, revision: document.revision, stepId: selected.id, sessionId: document.sessionId,
    sceneRevision: document.sceneRevision, epoch: document.epoch,
  } : undefined;
  const enabled = Boolean(client && !client.readOnly && plan?.target !== 'live'
    && connected && sameContext && selected && 'do' in selected);
  const identity = `${enabled}:${JSON.stringify(scope)}`;
  const [state, setState] = useState<{ identity?: string; value?: MotionPreview; error?: string }>({});
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!enabled || !scope) return;
    let disposed = false, pending: AbortController | undefined;
    const refresh = async () => {
      if (pending) return;
      const controller = new AbortController(); pending = controller;
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const value = await request<MotionPreview>(`/plans/${encodeURIComponent(scope.planId)}/steps/${scope.stepId}/preview?revision=${scope.revision}`,
          { signal: controller.signal });
        if (!previewMatches(value, scope)) throw new Error('预览已过期或几何无效，请重新选择步骤');
        if (!disposed) setState({ identity, value });
      } catch (error) { if (!disposed) setState({ identity, error: (error as Error).message }); }
      finally { clearTimeout(timeout); pending = undefined; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 1000);
    return () => { disposed = true; clearInterval(timer); pending?.abort(); };
  }, [enabled, identity, refreshKey, retry]); // Identity contains every immutable scope field.
  const response = state.identity === identity && enabled ? state : {};
  const explanation = client?.readOnly ? '录制未包含路径预览，回放仅重现已记录的计划状态。' :
    plan?.target === 'live' ? '实况设备动作不提供仿真路径预览。' :
    !selected ? '选择步骤以查看预览。' : !('do' in selected) ? '等待与检查步骤不产生机械臂路径。' :
      !current ? '正在读取当前计划修订…' : !sameContext ? '计划所属的会话上下文已改变，无法读取路径预览。' :
        !connected ? '会话未连接，路径预览暂不可用。' : response.value && !response.value.available
          ? '此动作未提供机械臂路径预览。' : undefined;
  return { value: response.value?.available ? response.value : undefined, error: response.error,
    explanation, loading: enabled && !response.value && !response.error, retry: () => setRetry(value => value + 1) };
}
