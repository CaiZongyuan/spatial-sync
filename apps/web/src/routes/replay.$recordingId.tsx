import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';
import { Workbench } from '../components/Workbench';
import { ReplayControls } from '../components/ReplayControls';
import { ReplaySource } from '../lib/replay/replay-source';

export const Route = createFileRoute('/replay/$recordingId')({
  validateSearch: (search: Record<string, unknown>) => ({
    src: typeof search.src === 'string' ? search.src : undefined,
  }),
  component: Replay,
});

function Replay() {
  const { recordingId } = Route.useParams();
  const { src } = Route.useSearch();
  const source = useMemo(() => new ReplaySource(recordingId, src), [recordingId, src]);
  return <Workbench sessionId={recordingId} source={source} controls={<ReplayControls source={source} />} />;
}
