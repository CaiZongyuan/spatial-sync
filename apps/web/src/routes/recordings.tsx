import { createFileRoute, Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { request } from '../lib/api';
import { bytes } from '../lib/platform';
import { readRecording, recordedScene, type Recording } from '../lib/replay/reader';

export const Route = createFileRoute('/recordings')({ component: Recordings });

function Recordings() {
  const [recordings, setRecordings] = useState<Recording[]>();
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(undefined);
    void request<unknown[]>('/recordings', { signal: controller.signal }).then(values => setRecordings(values.map(readRecording)))
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [attempt]);
  return <main className="home">
    <header className="page-heading"><span className="eyebrow">LABORATORY / HISTORY</span><h1>Recordings</h1>
      <p>Revisit the spatial state, device actions and events of a session.</p></header>
    {error ? <div className="platform-error" role="alert">{error} <button onClick={() => setAttempt(attempt + 1)}>Retry</button></div> :
      !recordings ? <div className="panel loading-state" role="status">Loading recordings…</div> :
        recordings.length === 0 ? <section className="panel empty-state"><span className="empty-orbit" aria-hidden="true">◷</span>
          <h2>Every experiment leaves a trace</h2><p>Sessions record automatically. Create a session, then return here to replay it.</p>
          <Link className="button-link primary" to="/sessions">Go to sessions →</Link></section> :
          <section className="platform-session-list recording-table" aria-label="Recordings">
            <table><thead><tr><th>Scene / recording</th><th>Status</th><th>Duration</th><th>Size</th><th>Open</th></tr></thead>
              <tbody>{recordings.map(recording => <tr key={recording.sessionId}>
                <td><strong title={recording.sceneContentHash ?? undefined}>{recordedScene(recording)}</strong><code>{recording.sessionId}</code><small>{recording.sceneRevision}</small></td>
                <td><span className={`state-pill ${recording.status === 'recording' ? 'running' : recording.status === 'incomplete' ? 'paused' : ''}`}>{recording.status}</span></td>
                <td>{recording.status === 'recording' ? 'In progress' : `${((recording.durationMs ?? 0) / 1000).toFixed(1)} s`}</td>
                <td>{bytes(recording.segments.reduce((total, segment) => total + segment.bytes, 0))}</td>
                <td><Link className="workspace-link" to="/replay/$recordingId" params={{ recordingId: recording.sessionId }} search={{ src: undefined }}>Replay →</Link></td>
              </tr>)}</tbody></table>
          </section>}
  </main>;
}
