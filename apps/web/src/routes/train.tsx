import { createFileRoute, Outlet } from '@tanstack/react-router';
import type { JobStatus } from '../lib/jobs';

export const Route = createFileRoute('/train')({
  validateSearch: (search: Record<string, unknown>): { q?: string; status?: JobStatus } => ({
    q: typeof search.q === 'string' ? search.q : undefined,
    status: typeof search.status === 'string' && ['queued', 'running', 'succeeded', 'failed', 'cancelled'].includes(search.status)
      ? search.status as JobStatus : undefined,
  }),
  component: Outlet,
});
