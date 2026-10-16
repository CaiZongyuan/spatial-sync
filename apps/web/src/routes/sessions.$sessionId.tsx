import { createFileRoute } from '@tanstack/react-router';
import { Workbench } from '../components/Workbench';

export const Route = createFileRoute('/sessions/$sessionId')({
  component: SessionPage,
});
function SessionPage() {
  const { sessionId } = Route.useParams();
  return <Workbench key={sessionId} sessionId={sessionId} />;
}
