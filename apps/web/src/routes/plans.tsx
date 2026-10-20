import { createFileRoute, Outlet } from '@tanstack/react-router';

export const Route = createFileRoute('/plans')({
  validateSearch: (search: Record<string, unknown>): { q?: string; status?: string; step?: number } => ({
    q: typeof search.q === 'string' ? search.q : undefined,
    status: typeof search.status === 'string' && ['draft', 'submitted', 'approved', 'running', 'completed', 'abandoned'].includes(search.status) ? search.status : undefined,
    step: Number.isInteger(Number(search.step)) && Number(search.step) > 0 ? Number(search.step) : undefined,
  }),
  component: Outlet,
});
