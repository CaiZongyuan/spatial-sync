import { Link, Outlet, useRouterState } from '@tanstack/react-router';
import { useCallback, useEffect, useState } from 'react';
import { ApprovalInboxContext, useApprovalInbox } from '../lib/approvals';
import '../approvals.css';
import { AlertContext, useAlerts } from '../lib/live';
import '../live.css';
import { Icon } from './Icon';
import { PlatformRuntimeContext } from '../lib/platform-runtime';
import { CostAnomalyContext, useCostAnomalies } from '../lib/cost';

type Theme = 'system' | 'light' | 'dark';
const themeKey = 'lab.theme';
const sidebarKey = 'lab.sidebar.collapsed';

function preference(key: string) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function remember(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* Preferences are optional. */ }
}

const pages = [
  { to: '/', label: 'Overview', text: '概览', icon: 'overview' },
  { to: '/live', label: 'Live', text: '实况', icon: 'live' },
  { to: '/assets', label: 'Assets', text: '素材库', icon: 'assets' },
  { to: '/scenes', label: 'Scenes', text: '场景库', icon: 'scenes' },
  { to: '/trash', label: 'Recovery', text: '回收站', icon: 'recordings' },
  { to: '/plans', label: 'Plans', text: '计划', icon: 'plans' },
  { to: '/train', label: 'Training', text: '训练', icon: 'training' },
  { to: '/eval', label: 'Evaluation', text: '评估', icon: 'evaluation' },
  { to: '/runtime-sessions', label: 'Runtime records', text: '运行记录', icon: 'recordings' },
  { to: '/cost', label: 'Cost', text: '成本', icon: 'cost' },
  { to: '/resources/gpu', label: 'Resources', text: '资源', icon: 'resources' },
  { to: '/approvals', label: 'Approvals', text: '待审批', icon: 'approvals' },
  { to: '/sessions', label: 'Sessions', text: '会话', icon: 'sessions' },
  { to: '/recordings', label: 'Recordings', text: '录制', icon: 'recordings' },
] as const;

export function PlatformShell() {
  const pathname = useRouterState({ select: state => state.location.pathname });
  const replay = pathname.startsWith('/replay/');
  const approvals = useApprovalInbox(!replay);
  const alertState = useAlerts(!replay);
  const costAnomalies = useCostAnomalies(!replay);
  const activeAlerts = alertState.active?.length;
  const alertCount = alertState.activeCapped ? '≥1000' : activeAlerts;
  const pendingCount = replay || approvals.error ? undefined : approvals.inbox?.pendingCount;
  const [collapsed, setCollapsed] = useState(() => preference(sidebarKey) === 'true');
  const [sourceVersions, setSourceVersions] = useState(new Map<string, string>());
  const reportSource = useCallback((owner: string, version?: string) => setSourceVersions(current => {
    if (current.get(owner) === version) return current;
    const next = new Map(current);
    if (version) next.set(owner, version); else next.delete(owner);
    return next;
  }), []);
  const versions = [...new Set(sourceVersions.values())];
  const [theme, setTheme] = useState<Theme>(() => {
    const value = preference(themeKey);
    return value === 'light' || value === 'dark' ? value : 'system';
  });
  useEffect(() => {
    if (theme === 'system') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    remember(themeKey, theme);
  }, [theme]);
  const current = replay ? pages.find(page => page.to === '/recordings')! :
    pages.find(page => page.to !== '/' && pathname.startsWith(page.to === '/resources/gpu' ? '/resources' : page.to)) ?? pages[0];
  const detail = pathname.startsWith('/replay/') || pathname.startsWith('/sessions/') || pathname.startsWith('/runtime-sessions/') || pathname.startsWith('/plans/') || pathname.startsWith('/train/jobs/') || pathname.startsWith('/eval/') || /^\/scenes\/[^/]+\/[^/]+/.test(pathname) || /^\/assets\/[^/]+\/[^/]+\/[^/]+/.test(pathname);
  const detailId = pathname.startsWith('/assets/') ? `${pathname.split('/').slice(-3, -1).join('/')}@${pathname.split('/').at(-1)}` : pathname.startsWith('/scenes/') ? pathname.split('/').slice(-2).join('@') : pathname.split('/').at(-1) ?? '';
  return <div className={`platform-shell ${collapsed ? 'is-collapsed' : ''}`}>
    <a className="skip-link" href="#page-content">Skip to content</a>
    <aside className="platform-sidebar" aria-label="Platform navigation">
      <Link to="/" className="platform-brand" aria-label="Lab platform home">
        <span className="platform-brand-mark"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.6l8.2 4.6L12 12.8 3.8 8.2z M3.8 8.2v7.4l8.2 4.6 8.2-4.6V8.2 M12 12.8v7.4" /></svg></span>
        <span className="sidebar-label"><strong>spatial-sync</strong><small>仪器控制台 · local</small></span>
      </Link>
      <nav aria-label="Main navigation">
        {pages.map(page => <Link key={page.to} to={page.to} className={`nav-item ${current.to === page.to ? 'is-current' : ''}`}
          aria-label={page.label} aria-current={current.to === page.to ? 'page' : undefined}>
          <Icon name={page.icon} /><span className="sidebar-label">{page.text}</span>
          {page.to === '/live' && activeAlerts !== undefined && activeAlerts > 0 &&
            <span className="live-nav-count" data-testid="live-alert-badge" aria-label={`${alertCount} 条活动告警`}>{alertCount}</span>}
          {page.to === '/approvals' && pendingCount !== undefined && pendingCount > 0 &&
            <span className="approval-nav-count" data-testid="approval-pending-badge" aria-label={`${pendingCount} 条待审批`}>{pendingCount}</span>}
          {page.to === '/cost' && Boolean(costAnomalies.data?.unreadCount) && <span className="cost-nav-dot" data-testid="cost-unread-badge"
            aria-label={`${costAnomalies.data!.unreadCount} 条未读成本异常`} />}
          <span className="nav-tooltip" role="tooltip">{page.text}</span>
        </Link>)}
      </nav>
      <div className="sidebar-footer">
        <dl className="sidebar-runtime sidebar-label"><div title="平台未提供 GPU 租约信息"><dt>GPU 租约</dt><dd>未知</dd></div>
          <div title={versions.length ? '来自当前活动视口的实际 Newton 源' : '当前页面没有活动 Newton 源的版本元数据'}>
            <dt>Newton</dt><dd>{versions.length === 1 ? versions[0] : versions.length ? '多个版本' : '未知'}</dd></div></dl>
        <label className="theme-picker" title="Color theme">
          <Icon name="theme" /><span className="sidebar-label">主题</span>
          <select aria-label="Color theme" value={theme} onChange={event => setTheme(event.target.value as Theme)}>
            <option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option>
          </select>
        </label>
        <button className="sidebar-collapse" aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-expanded={!collapsed} onClick={() => {
            setCollapsed(!collapsed); remember(sidebarKey, String(!collapsed));
          }}><Icon name="collapse" /><span className="sidebar-label">收起侧栏</span></button>
      </div>
    </aside>
    <div className={`platform-page ${detail ? 'is-detail' : ''}`}>
      <header className="page-topbar">
        <nav aria-label="Breadcrumb"><Link to="/">spatial-sync</Link><span>›</span>
          <Link to={current.to}>{current.text}</Link>
          {detail && <><span>/</span><span className="breadcrumb-id">{decodeURIComponent(detailId)}</span></>}
          {pathname === '/resources/gpu' && <><span>/</span><span>Compute</span></>}
          {pathname === '/resources/models' && <><span>/</span><span>模型</span></>}
        </nav>
        {pendingCount !== undefined && <Link to="/approvals" className={`approval-topbar-chip ${pendingCount ? 'has-pending' : ''}`}
          aria-label={`待审批 ${pendingCount}`}><i aria-hidden="true" />待审批 {pendingCount}</Link>}
        {!replay && approvals.error && <span className="approval-topbar-chip" role="status">审批暂不可用</span>}
        {!replay && activeAlerts !== undefined && activeAlerts > 0 && <Link to="/live" className="live-topbar-chip">告警 {alertCount}</Link>}
        {!replay && alertState.error && <span className="approval-topbar-chip" role="status">告警暂不可用</span>}
      </header>
      <div id="page-content" tabIndex={-1}><PlatformRuntimeContext.Provider value={reportSource}><AlertContext.Provider value={alertState}><ApprovalInboxContext.Provider value={approvals}><CostAnomalyContext.Provider value={costAnomalies}><Outlet /></CostAnomalyContext.Provider></ApprovalInboxContext.Provider></AlertContext.Provider></PlatformRuntimeContext.Provider></div>
    </div>
  </div>;
}
