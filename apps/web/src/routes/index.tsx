import { createFileRoute } from '@tanstack/react-router';
import { PlatformOverview } from '../components/PlatformOverview';

export const Route = createFileRoute('/')({ component: () => <main className="home"><PlatformOverview page="overview" /></main> });
