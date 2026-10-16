import type { FrameSample } from '../apps/web/src/lib/performance/frame-recorder';

export function distribution(values: number[]) {
  if (!values.length || values.some((value) => !Number.isFinite(value)))
    throw new Error('A metric needs finite, nonempty samples');
  const sorted = [...values].sort((a, b) => a - b);
  const rank = (q: number) =>
    sorted[Math.max(0, Math.ceil(q * sorted.length) - 1)];
  return {
    count: sorted.length,
    min: sorted[0],
    p05: rank(0.05),
    p50: rank(0.5),
    p95: rank(0.95),
    max: sorted.at(-1)!,
    mean: sorted.reduce((sum, value) => sum + value, 0) / sorted.length,
  };
}

export function summarizeFrames(samples: FrameSample[], overflow: number) {
  if (overflow || samples.length < 3)
    throw new Error('Truncated or empty frame measurement');
  const intervals: number[] = [];
  const fpsWindows: number[] = [];
  let windowStart = samples[0].timeMs;
  let count = 0;
  for (const [i, sample] of samples.entries()) {
    if (i > 0) {
      const interval = sample.timeMs - samples[i - 1].timeMs;
      if (!(interval > 0)) throw new Error('Frame timestamps must increase');
      intervals.push(interval);
    }
    if (sample.timeMs - windowStart >= 1000) {
      fpsWindows.push((count * 1000) / (sample.timeMs - windowStart));
      windowStart = sample.timeMs;
      count = 0;
    }
    count++;
  }
  if (fpsWindows.length < 2)
    throw new Error(
      'At least two complete one-second FPS windows are required',
    );
  return {
    sampleCount: samples.length,
    measuredDurationMs: samples.at(-1)!.timeMs - samples[0].timeMs,
    renderFps: distribution(fpsWindows),
    frameIntervalMs: distribution(intervals),
    transformApplyMs: distribution(samples.map((sample) => sample.applyMs)),
    frameAgeSinceReceiveMs: distribution(
      samples.map((sample) => sample.frameAgeMs),
    ),
    drawCalls: distribution(samples.map((sample) => sample.drawCalls)),
    sceneObjects: distribution(samples.map((sample) => sample.objects)),
    zeroApplySamples: samples.filter((sample) => sample.applyMs === 0).length,
  };
}
