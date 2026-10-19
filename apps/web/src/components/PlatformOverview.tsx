import { Link } from '@tanstack/react-router';
import { usePlatform } from '../lib/use-platform';
import { bytes, percent } from '../lib/platform';
import { NewtonViewerButton } from './NewtonViewerButton';

const unknown = 'Not available';


function Metric({ title, value, children }: { title: string; value: string; children?: React.ReactNode }) {
  return <article className="platform-metric" aria-label={title}>
    <span>{title}</span><strong>{value}</strong><div>{children}</div>
  </article>;
}

export function PlatformOverview({ page }: { page: 'sessions' | 'resources' }) {
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
      <div><span className="eyebrow">LABORATORY / {page === 'sessions' ? 'SESSIONS' : 'RESOURCES'}</span>
        <h1 id="platform-title" aria-label={page === 'sessions' ? 'Sessions' : 'Compute resources'}>{page === 'sessions' ? '会话' : '资源'}</h1>
        <p>{page === 'sessions' ? 'Managed sessions, connected observers and native viewers.' : 'Gateway host resources and managed Newton backends.'}</p></div>
      <div className={`platform-sample ${error || stale ? 'is-stale' : ''}`} role="status">
        {!snapshot ? error ? 'Platform unavailable' : 'Loading platform…' : stale ? 'Sample is stale' : error ? 'Refresh failed' : 'Live · refreshes every 2 s'}
        {snapshot && <small>Sampled {new Date(snapshot.sampledAtMs).toLocaleTimeString()}</small>}
      </div>
    </div>
    {error && <div className="platform-error" role="alert">{error} {snapshot && 'Showing the last successful sample.'}</div>}
    {page === 'sessions' && <div className="platform-summary">
      {summaries.map(([label, value]) => <article key={label} aria-label={label}><strong>{value ?? unknown}</strong><span>{label}</span></article>)}
    </div>}
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
    <>
    <div className="platform-section-heading"><h2>{page === 'sessions' ? 'Managed sessions' : 'Managed backends'}</h2><span>{page === 'sessions' ? 'Stopped sessions stay in history. Newton viewer is available on the gateway machine.' : 'Processes owned by this gateway. Host totals include other applications.'}</span></div>
    <div className="platform-session-list">
      {!sessions?.length ? <p className="platform-empty">{snapshot ? page === 'sessions' ? 'No sessions yet. Create a new session below.' : 'No managed Newton backends yet.' : 'Session list has not been received.'}</p> :
        <table><thead><tr><th>Scene / session</th><th>State / clients</th><th>Source / backend</th><th title="Process CPU uses 100% per logical core">Process CPU / RSS</th><th>Open</th></tr></thead>
          <tbody>{sessions.map(session => <tr key={session.sessionId} data-testid={`platform-session-${session.sessionId}`}>
            <td><strong>{session.sceneName}</strong><code>{session.sessionId}</code><small>{session.sceneVersion ?? session.sceneId}</small></td>
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
    </>
  </section>;
}
