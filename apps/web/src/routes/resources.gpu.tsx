import { createFileRoute } from '@tanstack/react-router';
import { PlatformOverview } from '../components/PlatformOverview';

export const Route = createFileRoute('/resources/gpu')({
  component: () => <main className="home"><PlatformOverview page="resources" /></main>,
});
