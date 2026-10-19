import { createFileRoute } from '@tanstack/react-router';
import { Overview } from '../components/Overview';

export const Route = createFileRoute('/')({
  validateSearch: (search: Record<string, unknown>): { entity?: string; readouts?: false; render?: string } => ({
    entity: typeof search.entity === 'string' ? search.entity : undefined,
    readouts: search.readouts === false || search.readouts === 'false' ? false : undefined,
    render: typeof search.render === 'string' ? search.render : undefined,
  }),
  component: Overview,
});
