import { createFileRoute } from '@tanstack/react-router';
import { TrainPage } from '../components/TrainPage';

export const Route = createFileRoute('/train/')({ component: TrainPage });
