import { createFileRoute } from '@tanstack/react-router';
import { RobotResourcesPage } from '../components/RobotResources';

export const Route = createFileRoute('/resources/robots')({ component: RobotResourcesPage });
