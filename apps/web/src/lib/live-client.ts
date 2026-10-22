import { request } from './api';
import type { AlertRule } from './live';
import type { Catalog, EntitySnapshot, Invocation, JsonValue, ServerMessage } from './protocol';
import { SessionClient } from './stream/client';

export interface LiveStatus {
  id: string; name: string; state: string; error?: string | null;
  sceneRevision: string; epoch: number; simTime: number; sampleTimeMs: number; eventSeq: number;
  role: 'viewer' | 'operator'; broker: string;
  recording: { id: string | null; status: string; retentionDays: number };
  layout: string | null; placements: Record<string, string | null>;
  layoutContentHash: string | null; layoutError: string | null;
  rules: Record<string, Record<string, AlertRule>>;
}

interface EventBatch {
  events: { seq: number; message: ServerMessage }[]; lastSeq: number; gap: boolean;
}

interface LiveSnapshot {
  snapshot: EntitySnapshot;
  plans: Extract<ServerMessage, { type: 'plan.update'; update: 'full' }>[];
  cursor: number;
}

/** Live has an information stream and HTTP actions, independent of any simulation. */
export class LiveClient extends SessionClient {
  private abort = new AbortController();
  private liveGeneration = 0;
  private refreshTimer?: ReturnType<typeof setTimeout>;
  private cursor = 0;
  private statusAt = 0;
  private liveStatus?: LiveStatus;
  getLiveStatus = () => this.liveStatus;

  constructor() { super('live'); }

  override async start() {
    this.stop();
    this.abort = new AbortController();
    const generation = ++this.liveGeneration;
    this.update({ connection: 'connecting', infoError: undefined, error: undefined });
    try {
      await this.bootstrap(generation);
      this.schedule(generation, 0);
    } catch (error) {
      if (this.current(generation)) {
        this.update({ connection: 'disconnected', error: (error as Error).message });
        this.schedule(generation, 1000);
      }
    }
  }

  override stop() {
    this.liveGeneration++;
    this.abort.abort();
    clearTimeout(this.refreshTimer);
    this.info.dispose();
  }

  private current(generation: number) { return generation === this.liveGeneration && !this.abort.signal.aborted; }

  private showStatus(status: LiveStatus) {
    this.liveStatus = status;
    this.statusAt = performance.now();
    this.update({ connection: 'connected', role: status.role, sessionState: status.state,
      simTime: status.simTime, error: status.error ?? undefined });
  }

  private async bootstrap(generation: number, status?: LiveStatus) {
    this.info.expectSnapshot();
    this.update({ connection: 'connecting' });
    const options = { signal: this.abort.signal };
    status ??= await request<LiveStatus>('/live', options);
    const [catalog, state] = await Promise.all([
      request<Catalog>('/live/catalog', options),
      request<LiveSnapshot>('/live/snapshot', options),
    ]);
    if (!this.current(generation)) return;
    if (catalog.sceneRevision !== status.sceneRevision || state.snapshot.sceneRevision !== status.sceneRevision
        || state.snapshot.epoch !== status.epoch)
      throw new Error('实况连接已更新，正在重新同步。');
    this.info.configure(status.sceneRevision, state.snapshot.epoch);
    this.plans.configure(status.sceneRevision, state.snapshot.epoch);
    this.info.setCatalog(catalog);
    this.applyGlobalMessage(state.snapshot);
    for (const plan of state.plans) this.plans.apply(plan);
    this.cursor = state.cursor;
    this.showStatus({ ...status, epoch: state.snapshot.epoch, simTime: state.snapshot.simTime,
      sampleTimeMs: state.snapshot.sampleTimeMs, eventSeq: state.cursor });
  }

  private schedule(generation: number, delay: number) {
    if (this.current(generation)) this.refreshTimer = setTimeout(() => { void this.refresh(generation); }, delay);
  }

  private async refresh(generation: number) {
    try {
      if (!this.liveStatus || !this.info.getMetadata().ready || performance.now() - this.statusAt >= 2000) {
        const status = await request<LiveStatus>('/live', { signal: this.abort.signal });
        if (!this.current(generation)) return;
        if (!this.liveStatus || !this.info.getMetadata().ready || status.id !== this.liveStatus.id
            || status.sceneRevision !== this.liveStatus.sceneRevision || status.epoch !== this.liveStatus.epoch) {
          await this.bootstrap(generation, status);
        } else this.showStatus(status);
      }
      const batch = await request<EventBatch>(`/live/events?after=${this.cursor}&timeoutSeconds=2`,
        { signal: this.abort.signal });
      if (!this.current(generation)) return;
      if (batch.gap || batch.lastSeq < this.cursor || batch.events.some(event =>
        'sceneRevision' in event.message && event.message.sceneRevision !== this.liveStatus?.sceneRevision)) {
        await this.bootstrap(generation);
      }
      else {
        for (const event of batch.events) {
          this.applyGlobalMessage(event.message);
          if ('epoch' in event.message) this.plans.advanceEpoch(event.message.epoch);
          if ('simTime' in event.message) this.update({ simTime: event.message.simTime });
        }
        this.cursor = batch.lastSeq;
        this.update({ connection: 'connected' });
      }
      this.schedule(generation, 0);
    } catch (error) {
      if (this.current(generation)) {
        this.update({ connection: 'disconnected', error: (error as Error).message });
        this.schedule(generation, 1000);
      }
    }
  }

  private requireOperator() {
    const state = this.getSnapshot();
    if (state.connection !== 'connected' || state.sessionState !== 'RUNNING' || !this.info.getMetadata().ready)
      throw new Error('等待实况设备重新连接后再操作。');
    if (state.role !== 'operator') throw new Error('Operator access is required.');
  }

  private send(path: string, body?: unknown) {
    this.requireOperator();
    const generation = this.liveGeneration;
    const context = this.liveStatus!;
    const sameTarget = () => this.current(generation) && this.liveStatus?.id === context.id
      && this.liveStatus.sceneRevision === context.sceneRevision;
    this.update({ infoError: undefined });
    void request<Invocation>(path, { method: 'POST', headers: { 'X-Actor': 'user:web' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: this.abort.signal })
      .then(invocation => {
        if (sameTarget()) this.info.apply({ ...invocation,
          type: 'invocation.update', sceneRevision: context.sceneRevision });
      }).catch((error: Error) => { if (sameTarget()) this.update({ infoError: error.message }); });
  }

  override action(entityKey: string, action: string, params: Record<string, JsonValue>) {
    const requestId = crypto.randomUUID();
    this.send('/live/actions', { requestId, entityKey, action, params });
    return requestId;
  }
  override cancelInvocation(id: string) { this.send(`/live/invocations/${encodeURIComponent(id)}/cancel`); }
  override approveInvocation(id: string, decision: 'approve' | 'deny') {
    this.send(`/live/invocations/${encodeURIComponent(id)}/approve`, { decision });
  }
  override command(): never { throw new Error('实况设备通过设备动作控制。'); }
}
