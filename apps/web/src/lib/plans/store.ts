import type { EntityEvent, OperationPlan, PlanUpdate } from '../protocol';
import { planChanges, type PlanChange } from './model';

export interface PlanAssociation { invocationIds: string[]; outOfOrder: boolean }
export interface PlanEntry {
  plan: OperationPlan;
  sceneRevision: string;
  epoch: number;
  simTime: number;
  sampleTimeMs: number;
  changes?: PlanChange[];
  associations: Record<number, PlanAssociation>;
}
export interface PlanDeviation { invocationId: string; outOfPlan: boolean; outOfOrder: boolean; simTime: number }

/** One immutable consumer for the live reliable FIFO and recorded FIFO. */
export class PlanStore {
  private revision = '';
  private epoch = -1;
  private entries = new Map<string, PlanEntry>();
  private listeners = new Set<() => void>();
  private state: { entries: PlanEntry[]; deviations: PlanDeviation[] } = { entries: [], deviations: [] };
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish(deviations = this.state.deviations) {
    this.state = { entries: [...this.entries.values()].sort((a, b) => b.sampleTimeMs - a.sampleTimeMs), deviations };
    this.listeners.forEach(listener => listener());
  }
  configure(revision: string, epoch: number) {
    this.revision = revision;
    this.epoch = epoch;
    this.entries.clear();
    this.publish([]);
  }
  advanceEpoch(epoch: number) {
    if (epoch > this.epoch) this.configure(this.revision, epoch);
  }
  apply(message: PlanUpdate | EntityEvent) {
    if (message.sceneRevision !== this.revision || message.epoch < this.epoch) return;
    this.advanceEpoch(message.epoch);
    if (message.type === 'entity.event') {
      if (message.event !== 'plan.invocation') return;
      const { planId, revision, stepId, invocationId, outOfOrder, outOfPlan } = message.data;
      if (typeof invocationId !== 'string') return;
      const entry = typeof planId === 'string' ? this.entries.get(planId) : undefined;
      if (entry && entry.plan.revision !== revision) return;
      let deviations = this.state.deviations;
      if ((outOfPlan === true || outOfOrder === true) && !deviations.some(item => item.invocationId === invocationId))
        deviations = [{ invocationId, outOfPlan: outOfPlan === true, outOfOrder: outOfOrder === true,
          simTime: message.simTime }, ...deviations].slice(0, 40);
      if (entry && entry.plan.revision === revision && typeof stepId === 'number'
        && entry.plan.steps.some(step => step.id === stepId)) {
        const prior = entry.associations[stepId]?.invocationIds ?? [];
        this.entries.set(entry.plan.id, { ...entry, associations: { ...entry.associations, [stepId]: {
          invocationIds: prior.includes(invocationId) ? prior : [...prior, invocationId], outOfOrder: outOfOrder === true,
        } } });
      }
      this.publish(deviations);
      return;
    }
    const id = message.update === 'full' ? message.plan.id : message.planId;
    const prior = this.entries.get(id);
    if (prior && (message.sampleTimeMs < prior.sampleTimeMs
      || (message.update === 'full' ? message.plan.revision : message.revision) < prior.plan.revision)) return;
    if (message.update === 'full') {
      const sameRevision = prior?.plan.revision === message.plan.revision;
      const associations: PlanEntry['associations'] = {};
      for (const step of message.plan.steps) {
        const old = prior?.plan.steps.find(old => old.id === step.id);
        if (old && prior?.associations[step.id] && (sameRevision || old.status === 'done'))
          associations[step.id] = prior.associations[step.id];
      }
      this.entries.set(id, { plan: structuredClone(message.plan), sceneRevision: message.sceneRevision,
        epoch: message.epoch, simTime: message.simTime, sampleTimeMs: message.sampleTimeMs, associations,
        changes: sameRevision ? prior?.changes : prior ? planChanges(prior.plan, message.plan) : undefined });
    } else {
      if (!prior || message.revision !== prior.plan.revision
        || !prior.plan.steps.some(step => step.id === message.stepId)) return;
      this.entries.set(id, { ...prior, sampleTimeMs: message.sampleTimeMs, simTime: message.simTime,
        plan: { ...prior.plan, steps: prior.plan.steps.map(step => step.id === message.stepId
          ? { ...step, status: message.status } : step) } });
    }
    this.publish();
  }
}
