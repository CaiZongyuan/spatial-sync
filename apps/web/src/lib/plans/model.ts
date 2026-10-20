import type { Actor, JsonValue, OperationPlan, Pose, SceneManifest } from '../protocol';

export type PlanStep = OperationPlan['steps'][number];
export type PlanChange = { stepId?: number; change: 'added' | 'removed' | 'changed' | 'moved' | 'title' };
export interface StepResult {
  invocationId?: string;
  invocationIds?: string[];
  invocationStatus?: string;
  outOfOrder?: boolean;
  [key: string]: JsonValue | undefined;
}
export interface PlanDocument extends OperationPlan {
  sessionId: string;
  sceneRevision: string;
  epoch: number;
  requestedBy: Actor;
  createdAtMs: number;
  approvalId: string | null;
  approvalStatus: string | null;
  approvalMode: 'human' | 'auto';
  stepResults: Record<string, StepResult>;
  changes: PlanChange[];
  persistence: { status: string; error?: string | null };
}
export interface PreviewScope {
  planId: string;
  revision: number;
  stepId: number;
  sessionId: string;
  sceneRevision: string;
  epoch: number;
}
export type MotionPreview = PreviewScope & ({ available: false; reason: string } | {
  available: true;
  entityKey: string;
  simTime: number;
  sampleTimeMs: number;
  points: [number, number, number][];
  startJointPositions: number[];
  targetJointPositions: number[];
  targetPose: Pose;
  targetBodyPoses: Record<string, Pose>;
  policyDriven: boolean;
});
export type AvailablePreview = Extract<MotionPreview, { available: true }>;

export function previewBodies(manifest: SceneManifest, preview: AvailablePreview) {
  if (preview.policyDriven) return [];
  return manifest.nodes.filter((node): node is Extract<SceneManifest['nodes'][number], { kind: 'body' }> =>
    node.kind === 'body' && Object.hasOwn(preview.targetBodyPoses, node.entityKey))
    .map(node => ({ node, pose: preview.targetBodyPoses[node.entityKey] }));
}

const canonical = (value: unknown): string => JSON.stringify(value, (_key, item) =>
  item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
const content = ({ status: _status, ...step }: PlanStep) => canonical(step);

export function planChanges(before: OperationPlan, after: OperationPlan): PlanChange[] {
  const changes: PlanChange[] = before.title === after.title ? [] : [{ change: 'title' }];
  const old = new Map(before.steps.map((step, index) => [step.id, { step, index }]));
  for (const [index, step] of after.steps.entries()) {
    const prior = old.get(step.id);
    if (!prior) changes.push({ stepId: step.id, change: 'added' });
    else if (content(prior.step) !== content(step)) changes.push({ stepId: step.id, change: 'changed' });
    else if (prior.index !== index) changes.push({ stepId: step.id, change: 'moved' });
    old.delete(step.id);
  }
  for (const stepId of old.keys()) changes.push({ stepId, change: 'removed' });
  return changes;
}

export function previewMatches(value: MotionPreview, scope: PreviewScope): boolean {
  if ((Object.keys(scope) as (keyof PreviewScope)[]).some(key => value[key] !== scope[key])) return false;
  if (!value.available) return true;
  const vector = (point: number[], length: number) => point.length === length && point.every(Number.isFinite);
  const pose = (value: Pose) => value && vector(value.p, 3) && vector(value.q, 4)
    && Math.abs(Math.hypot(...value.q) - 1) < .001;
  return value.points.length <= 64 && (value.policyDriven || value.points.length > 0)
    && value.points.every(point => vector(point, 3)) && pose(value.targetPose)
    && Object.values(value.targetBodyPoses).every(pose);
}

export const stepKind = (step: PlanStep) => 'do' in step ? 'do' : 'wait' in step ? 'wait' : 'check';
export const stepEntity = (step: PlanStep) => 'do' in step ? step.do.entity :
  String(('wait' in step ? step.wait : step.check).entity ?? '');

export function stepCommand(plan: OperationPlan, step: PlanStep, shell: 'powershell' | 'bash'): string {
  const quote = (value: string) => /^[a-zA-Z0-9_./:@,+=-]+$/.test(value) ? value :
    `'${value.replaceAll("'", shell === 'powershell' ? "''" : "'\\''")}'`;
  const suffix = ['--plan', plan.id, '--step', String(step.id), '--plan-revision', String(plan.revision)];
  if ('do' in step) return ['lw', 'do', step.do.entity, step.do.action,
    ...Object.entries(step.do.params).map(([key, value]) => `${key}=${JSON.stringify(value)}`),
    ...suffix].map(quote).join(' ');
  const condition = 'wait' in step ? step.wait : step.check;
  const operator = ['equals', 'notEquals', 'lt', 'lte', 'gt', 'gte',
    'in', 'notIn', 'isNull', 'notNull', 'stale', 'deviatesFrom'].find(key => key in condition);
  if (!operator) return '';
  const value = condition[operator];
  return ['lw', 'wait' in step ? 'wait' : 'check', String(condition.entity), String(condition.property),
    operator, typeof value === 'string' ? value : JSON.stringify(value),
    ...('by' in condition ? ['--by', String(condition.by)] : []),
    ...('wait' in step && step.timeoutSeconds != null ? ['--timeout', String(step.timeoutSeconds)] : []),
    ...suffix].map(quote).join(' ');
}
