import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  distribution,
  summarizeFrames,
} from '../../tools/viewer_profile_stats';

test('real-render report counts complete windows and reports actual frame work', () => {
  const samples = Array.from({ length: 151 }, (_, index) => ({
    timeMs: index * 20,
    applyMs: 0.25,
    drawCalls: 30,
    objects: 100,
    frameAgeMs: 40,
  }));
  const result = summarizeFrames(samples, 0);
  assert.equal(result.renderFps.count, 3);
  assert.equal(result.renderFps.p50, 50);
  assert.equal(result.frameIntervalMs.p95, 20);
  assert.equal(result.measuredDurationMs, 3000);
  assert.equal(result.transformApplyMs.p95, 0.25);
  assert.equal(result.drawCalls.mean, 30);
});

test('invalid, zero-collected and truncated measurements cannot produce a pass', () => {
  assert.throws(() => distribution([]));
  assert.throws(() => distribution([1, NaN]));
  assert.throws(() => summarizeFrames([], 0));
  const sample = {
    timeMs: 0,
    applyMs: 1,
    drawCalls: 1,
    objects: 1,
    frameAgeMs: 1,
  };
  assert.throws(() => summarizeFrames([sample, sample, sample], 0));
  assert.throws(() =>
    summarizeFrames(
      [sample, { ...sample, timeMs: 1000 }, { ...sample, timeMs: 2000 }],
      1,
    ),
  );
});
