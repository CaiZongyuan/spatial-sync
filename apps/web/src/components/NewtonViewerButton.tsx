import { useEffect, useRef, useState } from 'react';
import { openNewtonViewer, type NewtonViewer } from '../lib/platform';

export function NewtonViewerButton({ sessionId, source, state, sceneRevision, backendPid, backendAlive, viewer }: {
  sessionId: string;
  source: string;
  state: string;
  sceneRevision?: string;
  backendPid?: number | null;
  backendAlive?: boolean;
  viewer?: NewtonViewer;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const opening = useRef<{ tab: Window; controller: AbortController } | undefined>(undefined);
  const latest = useRef({ sessionId, state, backendPid, backendAlive, sceneRevision });
  latest.current = { sessionId, state, backendPid, backendAlive, sceneRevision };
  const active = state === 'RUNNING' || state === 'PAUSED';
  useEffect(() => {
    setError(undefined);
    setPending(false);
    return () => {
      const owned = opening.current;
      opening.current = undefined;
      owned?.controller.abort();
      if (owned && !owned.tab.closed) owned.tab.close();
    };
  }, [sessionId, source, sceneRevision, backendPid, backendAlive, active]);
  if (source !== 'newton') return null;
  const unavailable = !active ? 'The session is not running.' : backendAlive === false
    ? 'The Newton backend is not available.' : viewer?.status === 'unavailable'
      ? viewer.reason ?? 'Newton viewer is not available.' : undefined;
  return <div className="newton-viewer-action">
    <button disabled={pending || Boolean(unavailable)} title={unavailable ?? 'Opens a new tab. Use this on the gateway machine.'}
      onClick={async () => {
        if (opening.current || unavailable) return;
        setError(undefined);
        // Reserve the tab during the trusted click, before asynchronous startup.
        const tab = window.open('about:blank', '_blank');
        if (!tab) { setError('Allow pop-ups to open the Newton viewer.'); return; }
        tab.opener = null;
        tab.document.title = 'Opening Newton viewer';
        tab.document.body.textContent = 'Opening Newton viewer…';
        const attempt = { tab, controller: new AbortController() };
        opening.current = attempt;
        setPending(true);
        const requestedSession = sessionId;
        const requestedPid = backendPid;
        try {
          // The backend reuses its viewer under the lifecycle lock. A sampled URL
          // may already belong to a replaced runner, so validate every click.
          const ready = await openNewtonViewer(sessionId, attempt.controller.signal);
          if (opening.current !== attempt) return;
          const current = latest.current;
          if (current.sessionId !== requestedSession || current.sceneRevision !== sceneRevision || !['RUNNING', 'PAUSED'].includes(current.state) || current.backendAlive === false ||
              (current.backendPid != null && current.backendPid !== requestedPid && current.backendPid !== ready.backendPid))
            throw new Error('The session changed while its viewer was opening. Try again.');
          const target = new URL(ready.url, location.href);
          if (!['http:', 'https:'].includes(target.protocol)) throw new Error('The viewer returned an invalid address.');
          if (!tab.closed) tab.location.replace(target.href);
        } catch (error) {
          if (opening.current !== attempt) return;
          if (!tab.closed) tab.close();
          setError((error as Error).message);
        } finally {
          if (opening.current === attempt) {
            opening.current = undefined;
            setPending(false);
          }
        }
      }}>
      {pending ? 'Opening viewer…' : 'Open Newton viewer'}
    </button>
    {unavailable && <small className="muted">{unavailable}</small>}
    {error && <small role="alert">{error}</small>}
  </div>;
}
