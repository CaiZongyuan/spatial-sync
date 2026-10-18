import { Link } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { bytes, getPlatform, percent, type PlatformSnapshot } from '../lib/platform';
import { NewtonViewerButton } from './NewtonViewerButton';

const unknown = 'Not available';

function usePlatform() {
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

function Metric({ title, value, children }: { title: string; value: string; children?: React.ReactNode }) {
  return <article className="platform-metric" aria-label={title}>
    <span>{title}</span><strong>{value}</strong><div>{children}</div>
  </article>;
}

export function PlatformOverview({ page }: { page: 'overview' | 'sessions' | 'resources' }) {
  const { snapshot, error, now } = usePlatform();
  const stale = snapshot && now - snapshot.sampledAtMs > Math.max(6000, snapshot.samplingIntervalSeconds * 3000);
  const system = snapshot?.system;
  const summaries = [
    ['Total sessions', snapshot?.summary.totalSessions],
    ['Running', snapshot?.summary.runningSessions],
    ['Paused', snapshot?.summary.pausedSessions],
    ['Connected sessions', snapshot?.summary.connectedSessions],
    ['Connected clients', snapshot?.summary.connectedClients],
    ['Newton backends alive', snapshot?.summary.newtonBackends],
  ] as const;
  const sessions = snapshot?.sessions.filter(session => page !== 'resources' || session.backend).sort((a, b) =>
    Number(['STOPPED', 'FAILED'].includes(a.state)) - Number(['STOPPED', 'FAILED'].includes(b.state)),
  );
  return <section className="platform-overview" aria-labelledby="platform-title" aria-busy={!snapshot && !error}>
    <div className="platform-heading">
      <div><span className="eyebrow">LABORATORY / {page === 'overview' ? 'WORKBENCH' : page === 'sessions' ? 'SESSIONS' : 'RESOURCES'}</span>
        <h1 id="platform-title">{page === 'overview' ? 'Your laboratory, at a glance.' : page === 'sessions' ? 'Sessions' : 'Compute resources'}</h1>
        <p>{page === 'overview' ? 'A shared world. One authoritative source of truth.' : page === 'sessions' ? 'Managed sessions, connected observers and native viewers.' : 'Gateway host resources and managed Newton backends.'}</p></div>
      <div className={`platform-sample ${error || stale ? 'is-stale' : ''}`} role="status">
        {!snapshot ? error ? 'Platform unavailable' : 'Loading platform…' : stale ? 'Sample is stale' : error ? 'Refresh failed' : 'Live · refreshes every 2 s'}
        {snapshot && <small>Sampled {new Date(snapshot.sampledAtMs).toLocaleTimeString()}</small>}
      </div>
    </div>
    {error && <div className="platform-error" role="alert">{error} {snapshot && 'Showing the last successful sample.'}</div>}
    <div className="platform-summary">
      {summaries.map(([label, value]) => <article key={label} aria-label={label}><strong>{value ?? unknown}</strong><span>{label}</span></article>)}
    </div>
    {page === 'resources' && <>
    <div className="resource-tabs"><span aria-current="page">Compute</span></div>
    <div className="platform-section-heading"><h2>Gateway machine</h2><span>Host totals, including other applications</span></div>
    <div className="platform-metrics">
      <Metric title="Host CPU" value={percent(system?.cpu.percent)}>
        <span>{system?.cpu.logicalCores == null ? unknown : `${system.cpu.logicalCores} logical cores`}</span>
        {system?.cpu.reason && <small>{system.cpu.reason}</small>}
      </Metric>
      <Metric title="Host RAM" value={percent(system?.memory.percent)}>
        <span>{bytes(system?.memory.usedBytes)} / {bytes(system?.memory.totalBytes)}</span>
        <small>{bytes(system?.memory.availableBytes)} available</small>
        {system?.memory.reason && <small>{system.memory.reason}</small>}
      </Metric>
      <Metric title="GPU utilization" value={system?.gpus.length === 1 ? percent(system.gpus[0].utilizationPercent) : system?.gpus.length ? `${system.gpus.length} devices` : unknown}>
        {system?.gpus.map(gpu => <div key={gpu.index}>GPU {gpu.index} · {gpu.name}<small>{percent(gpu.utilizationPercent)}{gpu.temperatureC == null ? '' : ` · ${gpu.temperatureC} °C`}</small></div>)}
        {system?.gpuMessage && <small>{system.gpuMessage}</small>}
      </Metric>
      <Metric title="GPU memory" value={system?.gpus.length === 1 ? bytes(system.gpus[0].memoryUsedBytes) : system?.gpus.length ? `${system.gpus.length} devices` : unknown}>
        {system?.gpus.map(gpu => <div key={gpu.index}>GPU {gpu.index}<small>{bytes(gpu.memoryUsedBytes)} / {bytes(gpu.memoryTotalBytes)}</small></div>)}
        {!system?.gpus.length && <span>{system?.gpuMessage ?? unknown}</span>}
      </Metric>
    </div>
    </>}
    {page !== 'overview' && <>
    <div className="platform-section-heading"><h2>{page === 'sessions' ? 'Managed sessions' : 'Managed backends'}</h2><span>{page === 'sessions' ? 'Stopped sessions stay in history. Newton viewer is available on the gateway machine.' : 'Processes owned by this gateway. Host totals include other applications.'}</span></div>
    <div className="platform-session-list">
      {!sessions?.length ? <p className="platform-empty">{snapshot ? page === 'sessions' ? 'No sessions yet. Create a new session below.' : 'No managed Newton backends yet.' : 'Session list has not been received.'}</p> :
        <table><thead><tr><th>Scene / session</th><th>State / clients</th><th>Source / backend</th><th title="Process CPU uses 100% per logical core">Process CPU / RSS</th><th>Open</th></tr></thead>
          <tbody>{sessions.map(session => <tr key={session.sessionId} data-testid={`platform-session-${session.sessionId}`}>
            <td><strong>{session.sceneName}</strong><code>{session.sessionId}</code><small>{session.sceneId}</small></td>
            <td><span className={`state-pill ${session.state.toLowerCase()}`}>{session.state}</span><span>{session.connectedClients} connected {session.connectedClients === 1 ? 'client' : 'clients'}</span>
              <small>{session.simTime == null ? unknown : `${session.simTime.toFixed(2)} s`} · RTF {session.actualRtf == null ? unknown : session.actualRtf.toFixed(2)}</small></td>
            <td><strong className="platform-source">{session.source}</strong>
              <span>{session.backend ? `${session.backend.alive ? 'Alive' : 'Stopped'} · PID ${session.backend.pid ?? unknown}` : 'No separate backend'}</span>
              <small>{session.backend?.device ?? (session.source === 'newton' ? unknown : 'In-process source')}</small>
              {session.backend?.viewer.clientCount != null && <small>{session.backend.viewer.clientCount} native viewer clients</small>}</td>
            <td><span>{percent(session.backend?.cpuPercent)} / {bytes(session.backend?.memoryBytes)}</span>
              {session.backend?.metricsReason && <small>{session.backend.metricsReason}</small>}</td>
            <td><Link className="workspace-link" to="/sessions/$sessionId" params={{ sessionId: session.sessionId }}>Open workspace →</Link>
              <NewtonViewerButton sessionId={session.sessionId} source={session.source} state={session.state}
                backendPid={session.backend?.pid} backendAlive={session.backend?.alive} viewer={session.backend?.viewer} />
            </td>
          </tr>)}</tbody>
        </table>}
    </div>
    </>}
    {page === 'overview' && <div className="overview-grid">
      <section className="overview-session panel">
        <span className="eyebrow">ACTIVE WORLD</span><h2>Your shared workspace</h2>
        {sessions?.some(session => ['RUNNING', 'PAUSED'].includes(session.state)) ? sessions.filter(session => ['RUNNING', 'PAUSED'].includes(session.state)).map(session =>
          <div key={session.sessionId} className="overview-active-session"><span className={`state-pill ${session.state.toLowerCase()}`}>{session.state}</span>
            <h3>{session.sceneName}</h3><code>{session.sessionId}</code><p>{session.source} · {session.connectedClients} connected clients</p>
            <Link className="button-link primary" to="/sessions/$sessionId" params={{ sessionId: session.sessionId }}>Open workspace →</Link></div>) :
          <div className="empty-state"><span className="empty-orbit" aria-hidden="true">◎</span><h3>{snapshot ? 'Ready for your next experiment' : 'Connecting to the gateway'}</h3>
            <p>{snapshot ? 'Create a session to inspect a scene and follow its authoritative spatial state.' : 'The current session will appear after the platform sample arrives.'}</p>
            <Link className="button-link primary" to="/sessions">Go to sessions →</Link></div>}
      </section>
      <section className="panel overview-inspector"><span className="eyebrow">INSPECTOR</span><h2>Platform</h2>
        <dl className="overview-kv"><div><dt>Managed sessions</dt><dd>{snapshot?.summary.totalSessions ?? unknown}</dd></div>
          <div><dt>Connected observers</dt><dd>{snapshot?.summary.connectedClients ?? unknown}</dd></div>
          <div><dt>Newton backends</dt><dd>{snapshot?.summary.newtonBackends ?? unknown}</dd></div></dl>
        <Link to="/resources/gpu" className="workspace-link">Inspect compute resources →</Link>
        <Link to="/recordings" className="workspace-link">Browse recordings →</Link>
      </section>
    </div>}
  </section>;
}
