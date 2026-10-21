import { diagnosticMark } from './observer-diagnostic';
import type {
  SceneManifest,
  ServerMessage,
  CommandRequest,
  Catalog,
  JsonValue,
  ClientMessage,
} from '../protocol';
import { request } from '../api';
import { validateManifest } from '../validate-manifest';
import { PoseBuffer } from './pose-buffer';
import { InfoStore } from '../info/store';
import { PlanStore } from '../plans/store';

export interface ViewState {
  evaluationId?: string;
  manifest?: SceneManifest;
  role: 'viewer' | 'operator';
  infoError?: string;
  connection: 'connecting' | 'connected' | 'disconnected';
  sessionState: string;
  simTime: number;
  actualRtf: number;
  realtimeFactor: number | null;
  pending: string[];
  commandResult?: string;
  error?: string;
  stats: Record<string, unknown>;
  receivedFrames: number;
  payloadBytes: number;
  receivedRateHz: number;
  publishedRateHz?: number;
  gatewayHeartbeatMs?: number;
}

export class SessionClient {
  readonly readOnly: boolean = false;
  readonly buffer = new PoseBuffer();
  readonly info = new InfoStore();
  readonly plans = new PlanStore();
  private state: ViewState = {
    role: 'viewer',
    connection: 'connecting',
    sessionState: 'LOADING',
    simTime: 0,
    actualRtf: 0,
    realtimeFactor: 1,
    pending: [],
    stats: {},
    receivedFrames: 0,
    payloadBytes: 0,
    receivedRateHz: 0,
  };
  private listeners = new Set<() => void>();
  private ws?: WebSocket;
  private stopped = true;
  private retry?: ReturnType<typeof setTimeout>;
  private monitor?: ReturnType<typeof setInterval>;
  private statsPoll?: ReturnType<typeof setInterval>;
  private reconnectDelay = 500;
  private lastMessageAt = 0;
  private heartbeatIntervalMs = 1000;
  private gatewayHeartbeatAt?: number;
  private commandTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private rates: number[] = [];
  private generation = 0;
  private receivedFrames = 0;
  private payloadBytes = 0;
  private publishSample?: { frames: number; time: number; revision: unknown };
  private pendingSubscription?: string;
  private bodyIds = new Map<string, number>();
  private catalogCache?: Catalog;
  private catalogEtag?: string;

  constructor(readonly sessionId: string) {}
  getSnapshot = () => this.state;
  getStreamMetrics = () => ({
    timeMs: performance.now(),
    receivedFrames: this.receivedFrames,
    payloadBytes: this.payloadBytes,
    skippedNonFinite: this.buffer.skippedNonFinite,
    discardedFrames: this.buffer.discardedFrames,
    invalidBinaryFrames: this.buffer.invalidBinaryFrames,
    frameFormat: 'binary',
    revision: this.buffer.revision,
    epoch: this.buffer.epoch,
    seq: this.buffer.lastSeq,
    sessionState: this.state.sessionState,
    connection: this.state.connection,
    socketReadyState: this.ws?.readyState ?? WebSocket.CLOSED,
  });
  isEntityVisible(entityKey: string): boolean {
    if (!this.state.manifest) return false;
    const streamId = this.bodyIds.get(entityKey);
    return streamId === undefined || this.buffer.hasPose(streamId);
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  protected update(patch: Partial<ViewState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  samplePoses(now: number) {
    return this.buffer.sample(now);
  }

  protected configureManifest(manifest: SceneManifest) {
    this.bodyIds = new Map(
      manifest.nodes
        .filter((node) => node.kind === 'body')
        .map((node) => [node.entityKey, node.streamId]),
    );
    this.buffer.configure(manifest);
    this.info.configure(manifest.sceneRevision, manifest.epoch);
    this.plans.configure(manifest.sceneRevision, manifest.epoch);
    this.update({ manifest, error: undefined });
  }

  async start() {
    const generation = ++this.generation;
    this.stopped = false;
    document.addEventListener('visibilitychange', this.onVisibility);
    await this.loadManifest();
    if (this.stopped || generation !== this.generation) return;
    await this.loadStats();
    if (this.stopped || generation !== this.generation) return;
    this.connect();
    this.monitor = setInterval(() => {
      const now = performance.now();
      if (
        this.ws?.readyState === WebSocket.OPEN &&
        now - this.lastMessageAt > 3 * this.heartbeatIntervalMs
      )
        this.ws.close(4000, 'Heartbeat timeout');
      this.rates = this.rates.filter((time) => now - time < 1000);
      this.update({
        receivedRateHz: this.rates.length,
        simTime: this.buffer.displayedSimTime,
        receivedFrames: this.receivedFrames,
        payloadBytes: this.payloadBytes,
        gatewayHeartbeatMs:
          this.gatewayHeartbeatAt === undefined
            ? undefined
            : now - this.gatewayHeartbeatAt,
      });
    }, 250);
    this.statsPoll = setInterval(() => void this.loadStats(), 2000);
  }

  stop() {
    this.generation++;
    this.stopped = true;
    clearTimeout(this.retry);
    clearInterval(this.monitor);
    clearInterval(this.statsPoll);
    this.commandTimers.forEach(clearTimeout);
    this.commandTimers.clear();
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.ws?.close();
    this.info.dispose();
  }

  private onVisibility = () => {
    if (document.visibilityState === 'visible') {
      this.subscribeStream();
    }
  };

  private async loadManifest(generation = this.generation) {
    const manifest = validateManifest(
      await request<unknown>(
        `/sessions/${encodeURIComponent(this.sessionId)}/scene`,
      ),
    );
    if (this.stopped || generation !== this.generation) return;
    this.configureManifest(manifest);
  }

  private async loadCatalog(generation = this.generation) {
    const revision = this.state.manifest?.sceneRevision;
    if (!revision) return;
    try {
      const cached = this.catalogCache?.sceneRevision === revision;
      const response = await fetch(
        `/api/v1/sessions/${encodeURIComponent(this.sessionId)}/catalog`,
        {
          headers:
            cached && this.catalogEtag
              ? { 'If-None-Match': this.catalogEtag }
              : {},
        },
      );
      if (!response.ok && response.status !== 304)
        throw new Error(`Catalog unavailable (${response.status})`);
      const catalog =
        response.status === 304
          ? this.catalogCache
          : ((await response.json()) as Catalog);
      if (
        this.stopped ||
        generation !== this.generation ||
        revision !== this.state.manifest?.sceneRevision
      )
        return;
      if (!catalog || catalog.sceneRevision !== revision)
        throw new Error('Catalog scene revision does not match the workspace');
      this.catalogCache = catalog;
      this.catalogEtag = response.headers.get('ETag') ?? this.catalogEtag;
      this.info.setCatalog(catalog);
      this.update({ infoError: undefined });
    } catch (error) {
      if (!this.stopped && generation === this.generation)
        this.update({ infoError: (error as Error).message });
    }
  }

  private async loadStats() {
    try {
      const stats = await request<Record<string, unknown>>(
        `/sessions/${encodeURIComponent(this.sessionId)}/stats`,
      );
      if (!this.stopped) {
        const now = performance.now();
        let publishedRateHz: number | undefined;
        if (typeof stats.publishedFrames === 'number') {
          const previous = this.publishSample;
          if (
            previous &&
            previous.revision === stats.sceneRevision &&
            now > previous.time &&
            stats.publishedFrames >= previous.frames
          )
            publishedRateHz =
              ((stats.publishedFrames - previous.frames) * 1000) /
              (now - previous.time);
          this.publishSample = {
            frames: stats.publishedFrames,
            time: now,
            revision: stats.sceneRevision,
          };
        }
        this.update({ stats, publishedRateHz,
          evaluationId: typeof stats.evaluationId === 'string' ? stats.evaluationId : undefined });
      }
    } catch {
      /* Preserve the stream if a diagnostics request is temporarily unavailable. */
    }
  }

  private connect() {
    if (this.stopped) return;
    const generation = this.generation;
    this.update({ connection: 'connecting' });
    const url = new URL(
      `/api/v1/sessions/${encodeURIComponent(this.sessionId)}/ws`,
      location.href,
    );
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    let messages = Promise.resolve();
    let diagnosticFrames = 0;
    ws.onopen = () => {
      this.lastMessageAt = performance.now();
    };
    ws.onmessage = (event) => {
      const diagnostic = ++diagnosticFrames <= 3 || diagnosticFrames % 15 === 0;
      if (diagnostic) diagnosticMark('ws-enter', { binary: event.data instanceof ArrayBuffer, pending: Boolean(this.pendingSubscription) });
      this.lastMessageAt = performance.now();
      this.payloadBytes +=
        typeof event.data === 'string'
          ? new TextEncoder().encode(event.data).length
          : event.data.byteLength;
      messages = messages
        .then(async () => {
          if (this.stopped || this.ws !== ws) return;
          if (event.data instanceof ArrayBuffer) {
            const pending = Boolean(this.pendingSubscription);
            const accepted = !pending && this.buffer.pushBinary(event.data, performance.now());
            if (diagnostic) diagnosticMark('decision', { accepted, pending, seq: this.buffer.lastSeq, invalid: this.buffer.invalidBinaryFrames, discarded: this.buffer.discardedFrames });
            if (accepted) {
              if (diagnostic) diagnosticMark('accepted', { seq: this.buffer.lastSeq, epoch: this.buffer.epoch });
              this.receivedFrames++;
              this.plans.advanceEpoch(this.buffer.epoch);
              this.rates.push(performance.now());
              // Pose buffers stay outside React; paused views still need a draw after an authoritative sample.
              this.listeners.forEach(listener => listener());
            }
            return;
          }
          await this.receive(
            JSON.parse(event.data) as ServerMessage,
            generation,
          );
        })
        .catch((error: Error) => this.update({ error: error.message }));
    };
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      if (this.stopped || this.ws !== ws || generation !== this.generation)
        return;
      this.pendingSubscription = undefined;
      this.buffer.expectSnapshot();
      this.info.expectSnapshot();
      this.commandTimers.forEach(clearTimeout);
      this.commandTimers.clear();
      this.update({
        connection: 'disconnected',
        pending: [],
        commandResult: this.state.pending.length
          ? 'Connection lost before command acknowledgement'
          : this.state.commandResult,
      });
      this.retry = setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 5000);
    };
  }

  private subscribeStream() {
    if (this.ws?.readyState !== WebSocket.OPEN || !this.state.manifest) return;
    this.buffer.expectSnapshot();
    this.info.expectSnapshot();
    this.pendingSubscription = crypto.randomUUID();
    this.ws.send(
      JSON.stringify({
        type: 'client.subscribe',
        protocolVersion: 2,
        sceneRevision: this.state.manifest.sceneRevision,
        requestId: this.pendingSubscription,
        rateHz: this.state.manifest.source.basePublishHz,
        info: true,
      }),
    );
  }

  private async receive(message: ServerMessage, generation: number) {
    if (this.applyGlobalMessage(message)) return;
    switch (message.type) {
      case 'server.ready':
        diagnosticMark('ready', { state: message.sessionState });
        if (this.buffer.revision !== message.sceneRevision)
          await this.loadManifest();
        if (this.stopped || generation !== this.generation) return;
        this.heartbeatIntervalMs = message.heartbeatIntervalMs;
        this.buffer.paused = message.sessionState === 'PAUSED';
        this.update({
          connection: 'connected',
          role: message.role,
          sessionState: message.sessionState,
          simTime: message.simTime,
          realtimeFactor: message.realtimeFactor,
        });
        this.reconnectDelay = 500;
        void this.loadCatalog(generation);
        this.subscribeStream();
        break;
      case 'subscription.ack':
        diagnosticMark('ack', { matches: message.requestId === this.pendingSubscription });
        if (message.requestId !== this.pendingSubscription) break;
        this.pendingSubscription = undefined;
        this.buffer.grantedRateHz = message.grantedRateHz;
        break;
      case 'server.heartbeat':
        this.gatewayHeartbeatAt = performance.now();
        this.buffer.paused = message.state === 'PAUSED';
        this.update({
          sessionState: message.state,
          actualRtf: message.actualRtf,
          gatewayHeartbeatMs: 0,
        });
        break;
      case 'scene.replaced':
        this.pendingSubscription = undefined;
        this.buffer.revision = message.sceneRevision;
        this.buffer.expectSnapshot();
        this.update({
          manifest: undefined,
          sessionState: 'LOADING',
        });
        await this.loadManifest();
        if (this.stopped || generation !== this.generation) return;
        void this.loadCatalog(generation);
        this.subscribeStream();
        break;
      case 'command.ack': {
        clearTimeout(this.commandTimers.get(message.requestId));
        this.commandTimers.delete(message.requestId);
        this.update({
          pending: this.state.pending.filter((id) => id !== message.requestId),
          commandResult:
            message.status === 'applied'
              ? 'Command applied'
              : `Command rejected: ${message.reason}`,
        });
        break;
      }
    }
  }

  /** WebSocket delivery and recorded FIFO share this global state reducer. */
  protected applyGlobalMessage(message: ServerMessage): boolean {
    switch (message.type) {
      case 'session.state':
        if (message.sceneRevision !== this.buffer.revision) return true;
        this.plans.advanceEpoch(message.epoch);
        this.buffer.paused = message.state === 'PAUSED';
        if (this.buffer.paused) this.buffer.applyLatest();
        this.update({
          sessionState: message.state,
          simTime: message.simTime,
          actualRtf: message.actualRtf,
          realtimeFactor: message.realtimeFactor,
          ...(message.state === 'FAILED'
            ? { error: message.cause.message ?? message.cause.code ?? 'Source failed. Restart the session to recover.' }
            : {}),
        });
        return true;
      case 'entity.snapshot':
      case 'entity.observations':
      case 'entity.event':
      case 'invocation.update':
        this.info.apply(message);
        if (message.sceneRevision === this.buffer.revision) this.plans.advanceEpoch(message.epoch);
        if (message.type === 'entity.event') this.plans.apply(message);
        return true;
      case 'plan.update':
        this.plans.apply(message);
        return true;
      case 'server.error':
        this.update({ error: `${message.code}: ${message.message}` });
        return true;
      default:
        return false;
    }
  }

  private sendInfo(message: ClientMessage) {
    if (this.readOnly) throw new Error('Recorded sessions are read-only.');
    if (this.state.evaluationId) throw new Error('评估中，会话只读。');
    if (this.ws?.readyState !== WebSocket.OPEN)
      throw new Error('Connection unavailable. Retry after reconnect.');
    if (this.state.role !== 'operator')
      throw new Error('Operator access is required.');
    if (!this.info.getMetadata().ready)
      throw new Error('Waiting for device state to synchronize.');
    this.ws.send(JSON.stringify(message));
  }
  action(entityKey: string, action: string, params: Record<string, JsonValue>) {
    const requestId = crypto.randomUUID();
    this.sendInfo({
      type: 'action.request',
      requestId,
      entityKey,
      action,
      params,
    });
    return requestId;
  }
  cancelInvocation(invocationId: string) {
    this.sendInfo({
      type: 'action.cancel',
      requestId: crypto.randomUUID(),
      invocationId,
    });
  }
  approveInvocation(invocationId: string, decision: 'approve' | 'deny') {
    this.sendInfo({
      type: 'action.approve',
      requestId: crypto.randomUUID(),
      invocationId,
      decision,
    });
  }

  command(
    command: CommandRequest['command'],
    args: CommandRequest['args'] = {},
  ) {
    if (this.readOnly || this.state.evaluationId) return;
    if (this.ws?.readyState !== WebSocket.OPEN) {
      this.update({
        commandResult: 'Connection unavailable. Retry after reconnect.',
      });
      return;
    }
    const requestId = crypto.randomUUID();
    this.update({
      pending: [...this.state.pending, requestId],
      commandResult: undefined,
    });
    this.commandTimers.set(
      requestId,
      setTimeout(() => {
        this.commandTimers.delete(requestId);
        this.update({
          pending: this.state.pending.filter((id) => id !== requestId),
          commandResult: 'Command acknowledgement timed out',
        });
      }, 8000),
    );
    this.ws.send(
      JSON.stringify({
        type: 'command.request',
        requestId,
        command,
        args,
      } satisfies CommandRequest),
    );
  }
}
