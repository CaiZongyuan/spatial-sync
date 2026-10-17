import { useEffect, useRef, useState } from 'react';
import { openNewtonViewer, type NewtonViewer } from '../lib/platform';

export function NewtonViewerButton({ sessionId, source, state, backendPid, backendAlive, viewer }: {
  sessionId: string;
  source: string;
  state: string;
  backendPid?: number | null;
  backendAlive?: boolean;
  viewer?: NewtonViewer;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const [readyUrl, setReadyUrl] = useState<string>();
  const opening = useRef(false);
  const latest = useRef({ sessionId, state, backendPid, backendAlive });
  latest.current = { sessionId, state, backendPid, backendAlive };
  const active = state === 'RUNNING' || state === 'PAUSED';
  useEffect(() => {
    setReadyUrl(undefined);
    setError(undefined);
  }, [sessionId, backendPid, active]);
  if (source !== 'newton') return null;
  const unavailable = !active ? 'The session is not running.' : backendAlive === false
    ? 'The Newton backend is not available.' : viewer?.status === 'unavailable'
      ? viewer.reason ?? 'Newton viewer is not available.' : undefined;
  const url = viewer?.status === 'ready' ? viewer.url ?? readyUrl : readyUrl;
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
        opening.current = true;
        setPending(true);
        const requestedSession = sessionId;
        const requestedPid = backendPid;
        try {
          const ready = url ? { url, backendPid: requestedPid } : await openNewtonViewer(sessionId);
          const current = latest.current;
          if (current.sessionId !== requestedSession || !['RUNNING', 'PAUSED'].includes(current.state) || current.backendAlive === false ||
              (current.backendPid != null && ready.backendPid != null && current.backendPid !== ready.backendPid))
            throw new Error('The session changed while its viewer was opening. Try again.');
          const target = new URL(ready.url, location.href);
          if (!['http:', 'https:'].includes(target.protocol)) throw new Error('The viewer returned an invalid address.');
          setReadyUrl(target.href);
          if (!tab.closed) tab.location.replace(target.href);
        } catch (error) {
          if (!tab.closed) tab.close();
          setError((error as Error).message);
        } finally {
          opening.current = false;
          setPending(false);
        }
      }}>
      {pending ? 'Opening viewer…' : 'Open Newton viewer'}
    </button>
    {unavailable && <small className="muted">{unavailable}</small>}
    {error && <small role="alert">{error}</small>}
  </div>;
}
