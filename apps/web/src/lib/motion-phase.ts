export type Point = [number, number, number];
type Sample = { time: number; positions: readonly (readonly [number, number, number])[] };

export function phaseTime(elapsed: number, duration: number, loop: boolean) {
  const time = Math.max(0, elapsed);
  return loop && duration > 0 ? time % duration : Math.min(time, duration);
}

export function ghostPositionsAt(samples: readonly Sample[], time: number): Point[] {
  const endIndex = samples.findIndex(sample => sample.time > time);
  const end = samples[endIndex];
  const start = samples[Math.max(0, endIndex - 1)];
  if (!end || endIndex === 0) return (end ?? samples.at(-1))?.positions.map(point => [...point]) ?? [];
  const fraction = (time - start.time) / (end.time - start.time);
  return start.positions.map((point, index) => {
    const target = end.positions[index];
    return [point[0] + (target[0] - point[0]) * fraction,
      point[1] + (target[1] - point[1]) * fraction,
      point[2] + (target[2] - point[2]) * fraction];
  });
}
