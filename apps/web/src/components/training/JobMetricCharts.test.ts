import { describe, expect, it } from 'vitest';
import type { JobMetricSample } from '../../lib/jobs';
import { latestIterationSamples } from './JobMetricCharts';

describe('headline metric observations', () => {
  it('keeps actual zero/null and the terminal observation without adding an iteration for elapsed-only heartbeats', () => {
    const point = (iteration: number, elapsedSeconds: number, meanReward: number | null): JobMetricSample => ({
      iteration, elapsedSeconds, meanReward, trainSuccess: null, receivedAt: elapsedSeconds,
      reportedStatus: 'running', reason: null,
    });
    const observations = [point(0, 0, null), point(1, 3, 0), point(1, 4, 0), point(2, 5, 1),
      { ...point(2, 6, null), reportedStatus: 'cancelled' as const, reason: 'observed terminal reason' }];
    expect(latestIterationSamples(observations)).toEqual([observations[0], observations[2], observations[4]]);
  });
});
