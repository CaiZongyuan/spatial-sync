import { createFileRoute } from '@tanstack/react-router';
import { PlansPage } from '../components/PlansPage';

export const Route = createFileRoute('/plans/$planId')({ component: PlanDetail });
function PlanDetail() {
  const { planId } = Route.useParams();
  return <PlansPage planId={planId} />;
}
