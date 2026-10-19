import { createFileRoute } from '@tanstack/react-router';
import { ModelResources } from '../components/ModelResources';

export const Route = createFileRoute('/resources/models')({ component: ModelResources });
