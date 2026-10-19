import { useState, useSyncExternalStore } from 'react';
import type { ReplaySource } from '../lib/replay/replay-source';

const time = (milliseconds: number) => `${(milliseconds / 1000).toFixed(2)} s`;

export function ReplayControls({ source }: { source: ReplaySource }) {
  const playback = useSyncExternalStore(source.subscribePlayback, source.getPlayback);
  const [error, setError] = useState<string>();
  return (
    <div className="control-bar replay-controls">
      <span className="source-pill">Read-only replay · {playback.status ?? 'loading'}</span>
      {playback.kind && <span className="source-pill" title={playback.sceneContentHash ?? undefined}>
        {playback.kind === 'live' ? 'Live' : playback.sceneVersion ?? 'Unknown scene'}
      </span>}
      <button className="primary" disabled={!playback.ready || playback.seeking}
        onClick={() => playback.playing ? source.pause() : source.play()}>
        {playback.playing ? 'Pause replay' : 'Play replay'}
      </button>
      <label>Speed <select aria-label="Playback speed" value={playback.rate}
        onChange={(event) => source.setRate(Number(event.target.value))}>
        {[0.25, 0.5, 1, 2, 4].map((rate) => <option key={rate} value={rate}>{rate}×</option>)}
      </select></label>
      <input className="replay-seek" type="range" aria-label="Replay position"
        min={0} max={Math.ceil(playback.durationMs)} step={1} value={playback.seekTargetMs ?? playback.timeMs}
        disabled={!playback.ready}
        onChange={(event) => {
          setError(undefined);
          void source.seek(Number(event.target.value)).catch((error: Error) => setError(error.message));
        }} />
      <output data-testid="replay-time">{time(playback.timeMs)} / {time(playback.durationMs)}</output>
      {playback.seeking && <span role="status">Seeking…</span>}
      {playback.droppedFrames > 0 && <span>{playback.droppedFrames} dropped frames</span>}
      {error && <span role="alert">{error}</span>}
    </div>
  );
}
