import { createFileRoute } from '@tanstack/react-router';
import { JobDetailPage } from '../components/JobDetailPage';

export const Route = createFileRoute('/train/jobs/$id')({ component: JobDetail });

function JobDetail() {
  const { id } = Route.useParams();
  return <JobDetailPage jobId={id} />;
}
