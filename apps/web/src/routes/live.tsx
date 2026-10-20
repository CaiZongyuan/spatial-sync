import { createFileRoute } from '@tanstack/react-router';
import { LivePage } from '../components/LivePage';

export const Route = createFileRoute('/live')({
  validateSearch: (search: Record<string, unknown>): { entity?: string; readouts?: false } => ({
    entity: typeof search.entity === 'string' ? search.entity : undefined,
    readouts: search.readouts === false || search.readouts === 'false' ? false : undefined,
  }),
  component: LivePage,
});
