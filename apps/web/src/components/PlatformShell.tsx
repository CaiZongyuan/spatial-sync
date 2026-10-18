import { Link, Outlet, useRouterState } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

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
  { to: '/', label: 'Overview', icon: 'overview' },
  { to: '/resources/gpu', label: 'Resources', icon: 'resources' },
  { to: '/sessions', label: 'Sessions', icon: 'sessions' },
  { to: '/recordings', label: 'Recordings', icon: 'recordings' },
] as const;

function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    overview: 'M3 3h6v6H3z M15 3h6v6h-6z M3 15h6v6H3z M15 15h6v6h-6z',
    resources: 'M5 6h14v12H5z M9 2v4 M15 2v4 M9 18v4 M15 18v4 M1 10h4 M1 14h4 M19 10h4 M19 14h4 M9 10h6v4H9z',
    sessions: 'M4 4h16v16H4z M4 9h16 M9 9v11',
    recordings: 'M4 4h16v16H4z M10 8l6 4-6 4z',
    collapse: 'M14 6l-6 6 6 6',
  };
  return <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={paths[name]} /></svg>;
}

export function PlatformShell() {
  const pathname = useRouterState({ select: state => state.location.pathname });
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
  const current = pathname.startsWith('/replay/') ? pages[3] :
    pages.find(page => page.to !== '/' && pathname.startsWith(page.to === '/resources/gpu' ? '/resources' : page.to)) ?? pages[0];
  const detail = pathname.startsWith('/replay/') || pathname.startsWith('/sessions/');
  return <div className={`platform-shell ${collapsed ? 'is-collapsed' : ''}`}>
    <a className="skip-link" href="#page-content">Skip to content</a>
    <aside className="platform-sidebar" aria-label="Platform navigation">
      <Link to="/" className="platform-brand" aria-label="Lab platform home">
        <span className="platform-brand-mark">L<span>·</span></span>
        <span className="sidebar-label"><strong>Laboratory</strong><small>Spatial workbench</small></span>
      </Link>
      <nav aria-label="Main navigation">
        {pages.map(page => <Link key={page.to} to={page.to} className={`nav-item ${current.to === page.to ? 'is-current' : ''}`}
          aria-label={page.label} aria-current={current.to === page.to ? 'page' : undefined}>
          <Icon name={page.icon} /><span className="sidebar-label">{page.label}</span>
          <span className="nav-tooltip" role="tooltip">{page.label}</span>
        </Link>)}
      </nav>
      <div className="sidebar-footer">
        <label className="theme-picker" title="Color theme">
          <span aria-hidden="true">◐</span><span className="sidebar-label">Theme</span>
          <select aria-label="Color theme" value={theme} onChange={event => setTheme(event.target.value as Theme)}>
            <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
          </select>
        </label>
        <button className="sidebar-collapse" aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-expanded={!collapsed} onClick={() => {
            setCollapsed(!collapsed); remember(sidebarKey, String(!collapsed));
          }}><Icon name="collapse" /><span className="sidebar-label">Collapse sidebar</span></button>
      </div>
    </aside>
    <div className={`platform-page ${detail ? 'is-detail' : ''}`}>
      <header className="page-topbar">
        <nav aria-label="Breadcrumb"><Link to="/">Lab</Link><span>/</span>
          <Link to={current.to}>{current.label}</Link>
          {detail && <><span>/</span><span className="breadcrumb-id">{decodeURIComponent(pathname.split('/').at(-1) ?? '')}</span></>}
          {pathname === '/resources/gpu' && <><span>/</span><span>Compute</span></>}
        </nav>
      </header>
      <div id="page-content" tabIndex={-1}><Outlet /></div>
    </div>
  </div>;
}
