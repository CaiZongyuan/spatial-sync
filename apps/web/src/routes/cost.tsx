import { createFileRoute } from '@tanstack/react-router';
import { CostPage } from '../components/cost/CostPage';
import { costFilters } from '../lib/cost';

export const Route = createFileRoute('/cost')({
  validateSearch: (input: Record<string, unknown>): Partial<ReturnType<typeof costFilters>> => ({
    ...costFilters(input), range: input.range == null ? undefined : costFilters(input).range,
  }),
  component: Page,
});
function Page() {
  const filters = costFilters(Route.useSearch()), navigate = Route.useNavigate();
  return <CostPage filters={filters} onFiltersChange={search => void navigate({ search, replace: true })} />;
}
