import { createFileRoute } from '@tanstack/react-router';
import { EvaluationCenter } from '../components/EvaluationCenter';

export const Route = createFileRoute('/eval/')({ component: EvaluationCenter });
