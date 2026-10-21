import { useEffect, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { cancelSessionRequest, getSessionRequest, request, type SessionRequest } from '../lib/api';
import { launchScene, type SceneSource } from '../lib/library';

async function abandonQueuedLaunch(id: string) {
  try {
    const value = await cancelSessionRequest(id);
    // Only a newly created session belonging to this exact ticket is released here.
    if (value.status === 'started' && value.sessionId)
      await request<void>(`/sessions/${encodeURIComponent(value.sessionId)}`, { method: 'DELETE', keepalive: true });
  } catch (error) {
    console.error(`Queued launch cleanup failed for ${id}`, error);
    throw error;
  }
}

export function SessionLaunchButton({ sceneVersion, source, disabled }: {
  sceneVersion: string; source: SceneSource; disabled: boolean;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [ticket, setTicket] = useState<SessionRequest>();
  const [error, setError] = useState<string>();
  const pendingId = useRef<string | undefined>(undefined);
  const mounted = useRef(true);
  const enter = (sessionId: string) => {
    pendingId.current = undefined;
    void navigate({ to: '/sessions/$sessionId', params: { sessionId } });
  };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      const id = pendingId.current;
      pendingId.current = undefined;
      // The helper reports a failure with its ticket ID after this component has left the page.
      if (id) void abandonQueuedLaunch(id).catch(() => {});
    };
  }, []);
  useEffect(() => {
    if (!ticket || !['queued', 'starting', 'cancelling'].includes(ticket.status)) return;
    const controller = new AbortController();
    const timer = setInterval(() => {
      void getSessionRequest(ticket.requestId, controller.signal).then(value => {
        if (controller.signal.aborted) return;
        setTicket(value);
        if (value.status === 'started' && value.sessionId) enter(value.sessionId);
        else if (['cancelled', 'expired', 'failed'].includes(value.status)) {
          pendingId.current = undefined; setBusy(false);
          if (value.status !== 'cancelled') setError(value.status === 'expired' ? '排队已过期，请重新启动。' : '排队启动失败。');
        }
      }).catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message); });
    }, 500);
    return () => { clearInterval(timer); controller.abort(); };
  }, [ticket?.requestId, ticket?.status]);
  return <>
    <button className="primary" disabled={disabled || busy} onClick={async () => {
      setBusy(true); setError(undefined); setTicket(undefined);
      try {
        const value = await launchScene(sceneVersion, source);
        if ('requestId' in value) {
          if (!mounted.current) { await abandonQueuedLaunch(value.requestId); return; }
          pendingId.current = value.requestId;
          setTicket(value);
        } else if (mounted.current) enter(value.sessionId);
      } catch (failure) { if (mounted.current) { setError((failure as Error).message); setBusy(false); } }
    }}>{ticket?.status === 'cancelling' ? '正在取消启动…' : ticket && ['queued', 'starting'].includes(ticket.status) ? '评估期间排队中…' : busy ? '正在启动…' : '启动会话'} <span aria-hidden="true">↗</span></button>
    {ticket && ['queued', 'starting', 'cancelling'].includes(ticket.status) && <div role="status">
      <p>{ticket.status === 'cancelling' ? '正在取消本次启动并关闭它的会话。' : ticket.status === 'starting' ? '正在启动会话，可以取消本次启动。' : '评估占用会话，启动请求已排队。'}<code>{ticket.requestId}</code></p>
      <button disabled={ticket.status === 'cancelling'} onClick={async () => {
        try {
          const value = await cancelSessionRequest(ticket.requestId);
          setTicket(value);
          if (value.status === 'started' && value.sessionId) enter(value.sessionId);
          else if (value.status === 'cancelled') { pendingId.current = undefined; setBusy(false); }
        } catch (failure) { setError((failure as Error).message); }
      }}>取消排队</button>
    </div>}
    {ticket?.status === 'cancelled' && <p role="status">启动请求已取消。</p>}
    {error && <p className="scene-failure" role="alert">{error}</p>}
  </>;
}
