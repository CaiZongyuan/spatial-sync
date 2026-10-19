import { createFileRoute } from '@tanstack/react-router';
import { Workbench } from '../components/Workbench';

export const Route = createFileRoute('/sessions/$sessionId')({
  validateSearch: (search: Record<string, unknown>): { camera?: 'isometric'; render?: string } => ({
    camera: search.camera === 'isometric' ? 'isometric' as const : undefined,
    render: typeof search.render === 'string' ? search.render : undefined,
  }),
  component: SessionPage,
});
function SessionPage() {
  const { sessionId } = Route.useParams();
  return <Workbench key={sessionId} sessionId={sessionId} />;
}
