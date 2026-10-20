import { createFileRoute } from '@tanstack/react-router';
import { useMemo, useSyncExternalStore } from 'react';
import { Workbench } from '../components/Workbench';
import { ReplayControls } from '../components/ReplayControls';
import { ReplaySource } from '../lib/replay/replay-source';

export const Route = createFileRoute('/replay/$recordingId')({
  validateSearch: (search: Record<string, unknown>): { src?: string; camera?: 'isometric'; render?: string; plan?: string; step?: number } => ({
    src: typeof search.src === 'string' ? search.src : undefined,
    camera: search.camera === 'isometric' ? 'isometric' as const : undefined,
    render: typeof search.render === 'string' ? search.render : undefined,
    plan: typeof search.plan === 'string' ? search.plan : undefined,
    step: Number.isInteger(Number(search.step)) && Number(search.step) > 0 ? Number(search.step) : undefined,
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
