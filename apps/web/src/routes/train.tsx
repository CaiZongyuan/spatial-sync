import { createFileRoute, Outlet } from '@tanstack/react-router';
import type { JobStatus } from '../lib/jobs';

export const Route = createFileRoute('/train')({
  validateSearch: (search: Record<string, unknown>): { q?: string; status?: JobStatus; goal?: string; layer?: 'agent' | 'embodied'; agent?: string } => ({
    layer: search.layer === 'agent' || search.layer === 'embodied' ? search.layer : undefined,
    agent: typeof search.agent === 'string' && /^agent\/[a-z0-9]+(?:-[a-z0-9]+)*$/.test(search.agent) ? search.agent : undefined,
    q: typeof search.q === 'string' ? search.q : undefined,
    status: typeof search.status === 'string' && ['queued', 'running', 'succeeded', 'failed', 'cancelled'].includes(search.status)
      ? search.status as JobStatus : undefined,
    goal: typeof search.goal === 'string' && /^goal\/[a-z0-9]+(?:-[a-z0-9]+)*$/.test(search.goal) ? search.goal : undefined,
  }),
  component: Outlet,
});
