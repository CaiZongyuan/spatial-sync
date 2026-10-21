import { createFileRoute } from '@tanstack/react-router';
import { PlansPage } from '../components/PlansPage';

export const Route = createFileRoute('/plans/')({ component: () => <PlansPage /> });
