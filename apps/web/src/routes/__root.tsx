import { createRootRoute, Link } from '@tanstack/react-router';
import { PlatformShell } from '../components/PlatformShell';

export const Route = createRootRoute({
  component: PlatformShell,
  errorComponent: ({ error }) => (
    <main className="error-page">
      <span className="eyebrow">SPATIAL SYNC</span>
      <h1>Unable to open the workspace</h1>
      <p>{error instanceof Error ? error.message : String(error)}</p>
      <Link to="/">Back to sessions</Link>
    </main>
  ),
  notFoundComponent: () => (
    <main className="error-page">
      <h1>Workspace not found</h1>
      <Link to="/">Back to sessions</Link>
    </main>
  ),
});
