import { createFileRoute } from '@tanstack/react-router';
import { ComputeResources } from '../components/ComputeResources';

export const Route = createFileRoute('/resources/gpu')({
  component: ComputeResources,
});
