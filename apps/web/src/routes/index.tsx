import { createFileRoute, Link, useNavigate } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { createSession, getScenes, request, type SceneSummary } from '../lib/api';
import type { Recording } from '../lib/replay/reader';
import { PlatformOverview } from '../components/PlatformOverview';

export const Route = createFileRoute('/')({ component: Home });

function Home() {
  const navigate = useNavigate();
  const [scenes, setScenes] = useState<SceneSummary[]>([]);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [sceneId, setSceneId] = useState('lab-demo');
  const [source, setSource] = useState('synthetic');
  const [continueWithoutObservers, setContinueWithoutObservers] =
    useState(true);
  const [sessionId, setSessionId] = useState('');
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    void request<Recording[]>('/recordings').then(setRecordings).catch(() => {});
    getScenes()
      .then(({ scenes }) => {
        setScenes(scenes);
        if (!scenes.some((scene) => scene.sceneId === 'lab-demo'))
          setSceneId(scenes[0]?.sceneId ?? '');
      })
      .catch((error: Error) => setError(error.message));
  }, []);
  const scene = scenes.find((item) => item.sceneId === sceneId);
  return (
    <main className="home">
      <header className="home-header">
        <Link to="/" className="brand">
          <span className="brand-mark">S</span> SPATIAL<span>SYNC</span>
        </Link>
        <span className="version-tag">ENGINEERING WORKBENCH · V1</span>
      </header>
      <PlatformOverview />
      <div className="platform-section-heading"><h2>Start or join a workspace</h2><span>Launch a new scene or connect to an existing session</span></div>
      <section className="launch-grid">
        <div className="launch-card">
          <div className="card-number">01 / START A SESSION</div>
          <h2>Open your workspace</h2>
          <p>Select a registered scene and its authoritative motion source.</p>
          <label>
            Scene
            <select
              aria-label="Scene"
              value={sceneId}
              onChange={(event) => {
                setSceneId(event.target.value);
                const sources =
                  scenes.find((scene) => scene.sceneId === event.target.value)
                    ?.sources ?? [];
                if (!sources.includes(source as 'synthetic' | 'newton'))
                  setSource(sources[0] ?? 'synthetic');
              }}
            >
              {scenes.map((scene) => (
                <option key={scene.sceneId} value={scene.sceneId}>
                  {scene.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Source
            <select
              aria-label="Source"
              value={source}
              onChange={(event) => setSource(event.target.value)}
            >
              {(scene?.sources ?? ['synthetic', 'newton']).map((kind) => (
                <option key={kind} value={kind}>
                  {kind === 'synthetic'
                    ? 'Synthetic · deterministic, no GPU'
                    : 'Newton · authoritative physics'}
                </option>
              ))}
            </select>
          </label>
          <div className="scene-description">
            {scene?.description ??
              'A shared world with streamed body poses and independently inspectable joint anchors.'}
          </div>
          <label className="observer-policy">
            <input
              type="checkbox"
              checked={continueWithoutObservers}
              onChange={(event) =>
                setContinueWithoutObservers(event.target.checked)
              }
            />
            Keep running without observers
          </label>
          <button
            className="primary launch-button"
            disabled={loading || !scene}
            onClick={async () => {
              setLoading(true);
              setError(undefined);
              try {
                const session = await createSession(
                  sceneId,
                  source,
                  continueWithoutObservers,
                );
                await navigate({
                  to: '/sessions/$sessionId',
                  params: { sessionId: session.sessionId },
                });
              } catch (error) {
                setError((error as Error).message);
                setLoading(false);
              }
            }}
          >
            {loading ? 'Opening workspace…' : 'Launch workspace'}{' '}
            <span>↗</span>
          </button>
        </div>
        <div className="connect-card">
          <div className="card-number">02 / JOIN AN EXISTING SESSION</div>
          <h2>
            Same world.
            <br />
            Another observer.
          </h2>
          <p>
            Connect to a running session. Every observer sees the same
            authoritative state and control results.
          </p>
          <label>
            Session ID
            <input
              aria-label="Session ID"
              placeholder="Paste a session ID"
              value={sessionId}
              onChange={(event) => setSessionId(event.target.value)}
            />
          </label>
          <button
            disabled={!sessionId.trim()}
            onClick={() =>
              void navigate({
                to: '/sessions/$sessionId',
                params: { sessionId: sessionId.trim() },
              })
            }
          >
            Connect to session →
          </button>
          <div className="connect-note">
            <span className="live-dot" /> Shared session · Independent camera
          </div>
        </div>
      </section>
      {recordings.length > 0 && <section className="recording-list">
        <h2>Recordings</h2>
        {recordings.map((recording) => <Link key={recording.sessionId}
          to="/replay/$recordingId" params={{ recordingId: recording.sessionId }} search={{ src: undefined }}>
          <strong>{recording.sceneId}</strong>
          <span>{recording.sessionId} · {recording.status}</span>
          <span>{((recording.durationMs ?? 0) / 1000).toFixed(1)} s · Replay →</span>
        </Link>)}
      </section>}
      {error && (
        <div role="alert" className="banner error">
          {error}
          {scenes.length === 0 && (
            <small>
              Check that the gateway is running, then refresh this page.
            </small>
          )}
        </div>
      )}
      <footer className="home-footer">
        <span>NEWTON → FASTAPI → THREE.JS</span>
        <span>WORLD COORDINATES IN METERS · QUATERNIONS XYZW</span>
      </footer>
    </main>
  );
}
