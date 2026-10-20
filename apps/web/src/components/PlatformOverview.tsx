import { Link } from '@tanstack/react-router';
import { usePlatform } from '../lib/use-platform';
import { bytes, percent } from '../lib/platform';
import { NewtonViewerButton } from './NewtonViewerButton';
import { sessionLabel } from '../lib/ui-labels';

const unknown = '未知';
const metricLabels: Record<string, string> = { 'Host CPU': '主机 CPU', 'Host RAM': '主机内存', 'GPU utilization': 'GPU 使用率', 'GPU memory': 'GPU 显存' };


function Metric({ title, value, children }: { title: string; value: string; children?: React.ReactNode }) {
  return <article className="platform-metric" aria-label={title}>
    <span>{metricLabels[title] ?? title}</span><strong>{value}</strong><div>{children}</div>
  </article>;
}

export function PlatformOverview({ page }: { page: 'sessions' | 'resources' }) {
  const { snapshot, error, now } = usePlatform();
  const stale = snapshot && now - snapshot.sampledAtMs > Math.max(6000, snapshot.samplingIntervalSeconds * 3000);
  const system = snapshot?.system;
  const summaries = [
    ['Total sessions', '全部会话', snapshot?.summary.totalSessions],
    ['Running', '运行中', snapshot?.summary.runningSessions],
    ['Paused', '已暂停', snapshot?.summary.pausedSessions],
    ['Connected sessions', '已连接会话', snapshot?.summary.connectedSessions],
    ['Connected clients', '观察者', snapshot?.summary.connectedClients],
    ['Newton backends alive', '活动 Newton 后端', snapshot?.summary.newtonBackends],
  ] as const;
  const sessions = snapshot?.sessions.filter(session => page !== 'resources' || session.backend).sort((a, b) =>
    Number(['STOPPED', 'FAILED'].includes(a.state)) - Number(['STOPPED', 'FAILED'].includes(b.state)),
  );
  return <section className="platform-overview" aria-labelledby="platform-title" aria-busy={!snapshot && !error}>
    <div className="platform-heading">
      <div>
        <h1 id="platform-title" aria-label={page === 'sessions' ? 'Sessions' : 'Compute resources'}>{page === 'sessions' ? '会话' : '资源'}</h1>
        <p>{page === 'sessions' ? '查看托管会话、观察者与原生查看器。' : '查看网关主机资源与托管的 Newton 后端。'}</p></div>
      <div className={`platform-sample ${error || stale ? 'is-stale' : ''}`} role="status">
        {!snapshot ? error ? '平台暂不可用' : '正在读取平台…' : stale ? '采样已过期' : error ? '刷新失败' : '实时 · 每 2 秒刷新'}
        {snapshot && <small>采样于 {new Date(snapshot.sampledAtMs).toLocaleTimeString()}</small>}
      </div>
    </div>
    {error && <div className="platform-error" role="alert">{error} {snapshot && '当前保留最近一次有效采样。'}</div>}
    {page === 'sessions' && <div className="platform-summary">
      {summaries.map(([label, text, value]) => <article key={label} aria-label={label}><strong>{value ?? unknown}</strong><span>{text}</span></article>)}
    </div>}
    {page === 'resources' && <>
    <div className="resource-tabs"><span aria-current="page">算力</span></div>
    <div className="platform-section-heading"><h2 aria-label="Gateway machine">网关主机</h2><span>主机总量，包含其他应用</span></div>
    <div className="platform-metrics">
      <Metric title="Host CPU" value={percent(system?.cpu.percent)}>
        <span>{system?.cpu.logicalCores == null ? unknown : `${system.cpu.logicalCores} 个逻辑核心`}</span>
        {system?.cpu.reason && <small>{system.cpu.reason}</small>}
      </Metric>
      <Metric title="Host RAM" value={percent(system?.memory.percent)}>
        <span>{bytes(system?.memory.usedBytes)} / {bytes(system?.memory.totalBytes)}</span>
        <small>可用 {bytes(system?.memory.availableBytes)}</small>
        {system?.memory.reason && <small>{system.memory.reason}</small>}
      </Metric>
      <Metric title="GPU utilization" value={system?.gpus.length === 1 ? percent(system.gpus[0].utilizationPercent) : system?.gpus.length ? `${system.gpus.length} 个设备` : unknown}>
        {system?.gpus.map(gpu => <div key={gpu.index}>GPU {gpu.index} · {gpu.name}<small>{percent(gpu.utilizationPercent)}{gpu.temperatureC == null ? '' : ` · ${gpu.temperatureC} °C`}</small></div>)}
        {system?.gpuMessage && <small>{system.gpuMessage}</small>}
      </Metric>
      <Metric title="GPU memory" value={system?.gpus.length === 1 ? bytes(system.gpus[0].memoryUsedBytes) : system?.gpus.length ? `${system.gpus.length} 个设备` : unknown}>
        {system?.gpus.map(gpu => <div key={gpu.index}>GPU {gpu.index}<small>{bytes(gpu.memoryUsedBytes)} / {bytes(gpu.memoryTotalBytes)}</small></div>)}
        {!system?.gpus.length && <span>{system?.gpuMessage ?? unknown}</span>}
      </Metric>
    </div>
    </>}
    <>
    <div className="platform-section-heading"><h2 aria-label={page === 'sessions' ? 'Managed sessions' : 'Managed backends'}>{page === 'sessions' ? '托管会话' : '托管后端'}</h2><span>{page === 'sessions' ? '已停止的会话仍保留在历史中；原生查看器在网关电脑上打开。' : '仅列出本网关托管的进程；主机总量包含其他应用。'}</span></div>
    <div className="platform-session-list">
      {!sessions?.length ? <p className="platform-empty">{snapshot ? page === 'sessions' ? <>还没有会话。<Link to="/scenes">去场景库启动 →</Link></> : '暂无托管的 Newton 后端。' : '正在读取会话列表。'}</p> :
        <table><thead><tr><th>场景 / 会话</th><th>状态 / 观察者</th><th>物理源 / 后端</th><th title="每个逻辑核心的进程 CPU 上限为 100%" className="number">进程 CPU / 内存</th><th>打开</th></tr></thead>
          <tbody>{sessions.map(session => <tr key={session.sessionId} data-testid={`platform-session-${session.sessionId}`}>
            <td><strong>{session.sceneName}</strong><code>{session.sessionId}</code><small>{session.sceneVersion ?? session.sceneId}</small></td>
            <td><span className={`state-pill ${session.state.toLowerCase()}`} data-state={session.state} title={session.state}>{sessionLabel(session.state)}</span><span>{session.connectedClients} 位观察者</span>
              <small>{session.simTime == null ? unknown : `${session.simTime.toFixed(2)} s`} · RTF {session.actualRtf == null ? unknown : session.actualRtf.toFixed(2)}</small></td>
            <td><strong className="platform-source">{session.source}</strong>
              <span>{session.backend ? `${session.backend.alive ? '活动' : '已停止'} · PID ${session.backend.pid ?? unknown}` : '网关内运行'}</span>
              <small>{session.backend?.device ?? (session.source === 'newton' ? unknown : '内置源')}</small>
              {session.backend?.viewer.clientCount != null && <small>{session.backend.viewer.clientCount} 位原生查看器观察者</small>}</td>
            <td className="number"><span>{percent(session.backend?.cpuPercent)} / {bytes(session.backend?.memoryBytes)}</span>
              {session.backend?.metricsReason && <small>{session.backend.metricsReason}</small>}</td>
            <td><Link className="workspace-link" aria-label="Open workspace →" to="/sessions/$sessionId" params={{ sessionId: session.sessionId }}>打开工作台 →</Link>
              <NewtonViewerButton sessionId={session.sessionId} source={session.source} state={session.state}
                backendPid={session.backend?.pid} backendAlive={session.backend?.alive} viewer={session.backend?.viewer} />
            </td>
          </tr>)}</tbody>
        </table>}
    </div>
    </>
  </section>;
}
