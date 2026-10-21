/** Sampled viewer checks shared by the live I5 observer and its counterexample tests. */
export type ComparisonReport = {
  locationComparisons: number;
  poseComparisons: number;
  poseComparisonsByTube: Record<string, number>;
  maxPoseDelta: number;
  failures: unknown[];
};

type TubeSample = {
  key: string;
  simTime: number;
  location: unknown;
  shown: unknown;
  pose: unknown;
  before: unknown;
  rendered: unknown;
};

function finitePoint(value: unknown): value is number[] {
  return Array.isArray(value) && value.length === 3 && [0, 1, 2].every((axis) => Number.isFinite(value[axis]));
}

export function observeTube(report: ComparisonReport, strikes: Map<string, number>, sample: TubeSample) {
  const { key, simTime, location, shown, pose, before, rendered } = sample;
  report.locationComparisons += 1;
  const poseValid = finitePoint(pose);
  const renderedValid = finitePoint(rendered);
  const still = poseValid && finitePoint(before)
    && Math.max(...pose.map((value, axis) => Math.abs(value - before[axis]))) < 1e-5;
  let delta: number | undefined;
  if (still && renderedValid) {
    delta = Math.max(...pose.map((value, axis) => Math.abs(value - rendered[axis])));
    report.poseComparisons += 1;
    report.poseComparisonsByTube[key] = (report.poseComparisonsByTube[key] ?? 0) + 1;
    report.maxPoseDelta = Math.max(report.maxPoseDelta, delta);
  }
  // Two consecutive disagreements allow asynchronous reads without accepting a missing tube.
  const disagrees = shown !== location || !poseValid || !renderedValid || (delta !== undefined && delta > 1e-4);
  strikes.set(key, disagrees ? (strikes.get(key) ?? 0) + 1 : 0);
  if (strikes.get(key) === 2)
    report.failures.push({ key, simTime, apiLocation: location, browserLocation: shown, delta, poseValid, renderedValid });
}
