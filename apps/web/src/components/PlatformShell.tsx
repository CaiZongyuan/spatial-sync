import { Link, Outlet, useRouterState } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { ApprovalInboxContext, useApprovalInbox } from '../lib/approvals';
import '../approvals.css';

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
  { to: '/assets', label: 'Assets', text: '素材库', icon: 'assets' },
  { to: '/scenes', label: 'Scenes', text: '场景库', icon: 'scenes' },
  { to: '/resources/gpu', label: 'Resources', text: '资源', icon: 'resources' },
  { to: '/approvals', label: 'Approvals', text: '待审批', icon: 'approvals' },
  { to: '/sessions', label: 'Sessions', text: '会话', icon: 'sessions' },
  { to: '/recordings', label: 'Recordings', text: '录制', icon: 'recordings' },
] as const;

function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    overview: 'M3 3h6v6H3z M15 3h6v6h-6z M3 15h6v6H3z M15 15h6v6h-6z',
    resources: 'M5 6h14v12H5z M9 2v4 M15 2v4 M9 18v4 M15 18v4 M1 10h4 M1 14h4 M19 10h4 M19 14h4 M9 10h6v4H9z',
    sessions: 'M4 4h16v16H4z M4 9h16 M9 9v11',
    scenes: 'M12 3l9 5-9 5-9-5z M3 8v9l9 5 9-5V8 M12 13v9',
    assets: 'M12 3l9 5v9l-9 5-9-5V8z M3 8l9 5 9-5 M12 13v9',
    recordings: 'M4 4h16v16H4z M10 8l6 4-6 4z',
    approvals: 'M8 4h12v17H4V4h4 M8 2h8v4H8z M8 13l3 3 6-6',
    collapse: 'M14 6l-6 6 6 6',
  };
  return <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={paths[name]} /></svg>;
}

export function PlatformShell() {
  const pathname = useRouterState({ select: state => state.location.pathname });
  const replay = pathname.startsWith('/replay/');
  const approvals = useApprovalInbox(!replay);
  const pendingCount = replay || approvals.error ? undefined : approvals.inbox?.pendingCount;
  const [collapsed, setCollapsed] = useState(() => preference(sidebarKey) === 'true');
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
  const detail = pathname.startsWith('/replay/') || pathname.startsWith('/sessions/') || /^\/scenes\/[^/]+\/[^/]+/.test(pathname) || /^\/assets\/[^/]+\/[^/]+\/[^/]+/.test(pathname);
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
          {page.to === '/approvals' && pendingCount !== undefined && pendingCount > 0 &&
            <span className="approval-nav-count" data-testid="approval-pending-badge" aria-label={`${pendingCount} 条待审批`}>{pendingCount}</span>}
          <span className="nav-tooltip" role="tooltip">{page.text}</span>
        </Link>)}
      </nav>
      <div className="sidebar-footer">
        <label className="theme-picker" title="Color theme">
          <span aria-hidden="true">◐</span><span className="sidebar-label">Theme</span>
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
        </nav>
        {pendingCount !== undefined && <Link to="/approvals" className={`approval-topbar-chip ${pendingCount ? 'has-pending' : ''}`}
          aria-label={`待审批 ${pendingCount}`}><i aria-hidden="true" />待审批 {pendingCount}</Link>}
        {!replay && approvals.error && <span className="approval-topbar-chip" role="status">审批暂不可用</span>}
      </header>
      <div id="page-content" tabIndex={-1}><ApprovalInboxContext.Provider value={approvals}><Outlet /></ApprovalInboxContext.Provider></div>
    </div>
  </div>;
}
