import { createFileRoute } from '@tanstack/react-router';
import { Workbench } from '../components/Workbench';

export const Route = createFileRoute('/sessions/$sessionId')({
  validateSearch: (search: Record<string, unknown>): { camera?: 'isometric'; render?: string; plan?: string; step?: number; view?: 'devices'; entity?: string; readouts?: false } => ({
    camera: search.camera === 'isometric' ? 'isometric' as const : undefined,
    render: typeof search.render === 'string' ? search.render : undefined,
    plan: typeof search.plan === 'string' ? search.plan : undefined,
    step: Number.isInteger(Number(search.step)) && Number(search.step) > 0 ? Number(search.step) : undefined,
    view: search.view === 'devices' ? 'devices' : undefined,
    entity: typeof search.entity === 'string' ? search.entity : undefined,
    readouts: search.readouts === false || search.readouts === 'false' ? false : undefined,
  }),
  component: SessionPage,
});
function SessionPage() {
  const { sessionId } = Route.useParams();
  return <Workbench key={sessionId} sessionId={sessionId} />;
}
