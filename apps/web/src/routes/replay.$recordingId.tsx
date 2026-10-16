import { createFileRoute } from '@tanstack/react-router';
import { useMemo, useSyncExternalStore } from 'react';
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
  const renderContinuously = useSyncExternalStore(source.subscribePlayback, () => {
    const playback = source.getPlayback();
    return playback.playing && !playback.seeking;
  });
  return <Workbench sessionId={recordingId} source={source} renderContinuously={renderContinuously}
    controls={<ReplayControls source={source} />} />;
}
