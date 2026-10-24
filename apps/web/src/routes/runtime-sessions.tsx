import { createFileRoute, Outlet } from '@tanstack/react-router';
export const Route = createFileRoute('/runtime-sessions')({
  validateSearch: (search: Record<string, unknown>): { q?: string } => ({ q: typeof search.q === 'string' ? search.q : undefined }),
  component: Outlet,
});
