import { createFileRoute } from '@tanstack/react-router';
import { EvaluationCenter } from '../components/EvaluationCenter';

export const Route = createFileRoute('/eval/$suiteId')({ component: SuiteDetail });
function SuiteDetail() {
  const { suiteId } = Route.useParams();
  return <EvaluationCenter key={suiteId} suiteId={`suite/${suiteId}`} />;
}
