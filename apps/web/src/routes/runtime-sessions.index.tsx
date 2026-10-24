import { createFileRoute } from '@tanstack/react-router';
import { RuntimeRecordsPage } from '../components/runtime/RuntimeRecordsPage';
export const Route = createFileRoute('/runtime-sessions/')({ component: Page });
function Page() {
  const search = Route.useSearch(), navigate = Route.useNavigate();
  return <RuntimeRecordsPage query={search.q} onQuery={q => void navigate({ search: { q: q || undefined }, replace: true })} />;
}
