import { expect, it } from 'vitest';
import { FrameRecorder } from './frame-recorder';

it('records real callback timestamps and work only inside the explicit measurement interval', () => {
  const recorder = new FrameRecorder();
  recorder.record(5, 999, 999, 999, 999);
  recorder.start();
  recorder.record(100, 0.25, 24, 40, 15);
  recorder.record(116, 0.5, 25, 42, 31);
  const result = recorder.stop();
  expect(result.samples).toEqual([
    { timeMs: 100, applyMs: 0.25, drawCalls: 24, objects: 40, frameAgeMs: 15 },
    { timeMs: 116, applyMs: 0.5, drawCalls: 25, objects: 42, frameAgeMs: 31 },
  ]);
  recorder.record(132, 999, 999, 999, 999);
  expect(result.samples).toHaveLength(2);
  recorder.start();
  expect(recorder.stop().samples).toEqual([]);
});

it('reports overflow rather than silently treating a truncated window as complete', () => {
  const recorder = new FrameRecorder(1);
  recorder.start();
  recorder.record(100, 0.1, 1, 1, 1);
  recorder.record(120, 0.2, 1, 1, 1);
  expect(recorder.stop()).toMatchObject({
    samples: [{ timeMs: 100 }],
    overflow: 1,
  });
});
