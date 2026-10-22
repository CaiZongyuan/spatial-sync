import { createFileRoute } from '@tanstack/react-router';
import { LivePage } from '../components/LivePage';

export const Route = createFileRoute('/live')({
  validateSearch: (search: Record<string, unknown>): { entity?: string; readouts?: false; plan?: string; step?: number } => ({
    entity: typeof search.entity === 'string' ? search.entity : undefined,
    readouts: search.readouts === false || search.readouts === 'false' ? false : undefined,
    plan: typeof search.plan === 'string' ? search.plan : undefined,
    step: Number.isInteger(Number(search.step)) && Number(search.step) > 0 ? Number(search.step) : undefined,
  }),
  component: LivePage,
});
