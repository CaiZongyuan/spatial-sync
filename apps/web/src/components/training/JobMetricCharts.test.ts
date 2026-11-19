import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { JobMetricSample } from '../../lib/jobs';
import { JobMetricCharts, latestIterationSamples } from './JobMetricCharts';

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

  it('plots measured zeros and the largest observed iteration despite index-free cleanup and terminal samples', () => {
    const zero: JobMetricSample = {
      receivedAt: 1, reportedStatus: 'running', reason: null,
      iteration: 0, elapsedSeconds: 0, meanReward: 0, trainSuccess: 0,
    };
    const finalIteration: JobMetricSample = {
      ...zero, receivedAt: 2, iteration: 2400, elapsedSeconds: 18, meanReward: 2.5, trainSuccess: 0.75,
    };
    const cleanup: JobMetricSample = { receivedAt: 3, reportedStatus: 'cleaned', reason: null, elapsedSeconds: 19 };
    const unmeasured: JobMetricSample = { receivedAt: 4, reportedStatus: 'failed', reason: 'owned exit' };
    const history = [zero, finalIteration, cleanup, unmeasured];
    expect(latestIterationSamples(history)).toEqual([zero, finalIteration]);
    expect(latestIterationSamples([unmeasured])).toEqual([]);

    const markup = renderToStaticMarkup(createElement(JobMetricCharts, { history }));
    expect(markup).toContain('2400 iteration');
    expect(markup.match(/<circle\b/g)).toHaveLength(4);
    expect(markup).toContain('<td>0</td><td>0.000</td><td>0.0%</td>');
    const noMeasurements = renderToStaticMarkup(createElement(JobMetricCharts, { history: [unmeasured] }));
    expect(noMeasurements).not.toContain('<svg');
  });
});
