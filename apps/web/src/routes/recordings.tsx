import { createFileRoute, Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { request } from '../lib/api';
import { bytes } from '../lib/platform';
import { readRecording, recordedScene, recordingUrl, type Recording } from '../lib/replay/reader';
import { Icon } from '../components/Icon';

export const Route = createFileRoute('/recordings')({ component: Recordings });
type RecordedSource = 'newton' | 'synthetic' | 'live' | 'unknown';
const sourceNames: Record<RecordedSource, string> = { newton: 'Newton', synthetic: '合成', live: '实况', unknown: '未知' };

function Recordings() {
  const [recordings, setRecordings] = useState<Recording[]>();
  const [sources, setSources] = useState<Record<string, RecordedSource>>({});
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError(undefined);
    void request<unknown[]>('/recordings', { signal: controller.signal }).then(values => setRecordings(values.map(readRecording)))
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    if (!recordings) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    setSources({});
    // The frozen Manifest is authoritative; scene capabilities cannot identify a recording's producer.
    void Promise.all(recordings.map(async recording => {
      let source: RecordedSource = recording.kind === 'live' ? 'live' : 'unknown';
      if (recording.kind === 'session') try {
        const documentUrl = new URL(`/recordings/${encodeURIComponent(recording.sessionId)}/recording.json`, location.href).href;
        const manifest = recording.revisions?.[recording.sceneRevision]?.manifest ?? 'manifest.json';
        const response = await fetch(recordingUrl(manifest, documentUrl), { signal: controller.signal });
        if (response.ok) {
          const value = await response.json();
          if (value.sessionId === recording.sessionId && value.sceneRevision === recording.sceneRevision
            && ['newton', 'synthetic'].includes(value.source?.kind)) source = value.source.kind;
        }
      } catch { /* An unavailable archived document leaves the source unknown. */ }
      if (!controller.signal.aborted) setSources(current => ({ ...current, [recording.sessionId]: source }));
    })).finally(() => clearTimeout(timeout));
    return () => { clearTimeout(timeout); controller.abort(); };
  }, [recordings]);
  return <main className="home">
    <header className="page-heading"><h1 aria-label="Recordings">录制</h1><p>沿时间轴回看会话中的空间状态、设备动作与事件。</p></header>
    {error ? <div className="platform-error" role="alert">{error} <button onClick={() => setAttempt(attempt + 1)}>重试</button></div> :
      !recordings ? <div className="panel loading-state" role="status">正在读取录制…</div> :
        recordings.length === 0 ? <section className="panel empty-state"><Icon name="recordings" className="empty-recording-icon" />
          <h2>每次实验都有迹可循</h2><p>会话会自动录制。启动会话后，即可在这里回放。</p>
          <Link className="button-link primary" aria-label="Go to sessions →" to="/sessions">前往会话 →</Link></section> :
          <section className="platform-session-list recording-table" aria-label="Recordings">
            <table><thead><tr><th>会话 ID</th><th>场景</th><th className="number">时长</th><th>来源</th><th className="number">大小</th><th>打开</th></tr></thead>
              <tbody>{recordings.map(recording => {
                const source = sources[recording.sessionId] ?? (recording.kind === 'live' ? 'live' : 'unknown');
                return <tr key={recording.sessionId}>
                  <td><code title={`场景修订 ${recording.sceneRevision} · ${recording.createdAt}`}>{recording.sessionId}</code></td>
                  <td title={recording.sceneContentHash ?? undefined}>{recordedScene(recording)}</td>
                  <td className="number">{recording.status === 'recording' ? <span className="state-pill running">录制中</span> : <>
                    {recording.durationMs == null ? '未知' : `${(recording.durationMs / 1000).toFixed(1)} s`}
                    {recording.status === 'incomplete' && <span className="recording-incomplete" title="录制不完整，保留已写入的样本">不完整</span>}</>}</td>
                  <td data-testid={`recording-source-${recording.sessionId}`} data-source={source}
                    title={source === 'unknown' ? '录制未包含可读取的物理源信息' : source}>{sourceNames[source]}</td>
                  <td className="number">{bytes(recording.segments.reduce((total, segment) => total + segment.bytes, 0))}</td>
                  <td><Link className="workspace-link" aria-label="Replay →" to="/replay/$recordingId" params={{ recordingId: recording.sessionId }} search={{ src: undefined }}>回放 →</Link></td>
                </tr>;
              })}</tbody></table>
          </section>}
  </main>;
}
