import { createFileRoute } from '@tanstack/react-router';
import { ApprovalsPage } from '../components/ApprovalsPage';

export const Route = createFileRoute('/approvals')({ component: ApprovalsPage });
