import type {
  SceneManifest,
  ServerMessage,
  CommandRequest,
  EntityState,
  Subscribe,
} from '../protocol';
import { request } from '../api';
import { validateManifest } from '../validate-manifest';
import { PoseBuffer } from './pose-buffer';

export type SubscriptionOptions = {
  scope: Subscribe['scope'];
  entityKeys?: string[];
  deviceKey?: string;
  viewport?: Subscribe['viewport'];
  delta?: boolean;
};

export interface ViewState {
  manifest?: SceneManifest;
  connection: 'connecting' | 'connected' | 'disconnected';
  sessionState: string;
  simTime: number;
  actualRtf: number;
  realtimeFactor: number | null;
  pending: string[];
  commandResult?: string;
  error?: string;
  entities: Record<string, EntityState>;
  stats: Record<string, unknown>;
  receivedFrames: number;
  payloadBytes: number;
  receivedRateHz: number;
  publishedRateHz?: number;
}

export class SessionClient {
  readonly buffer = new PoseBuffer();
  private state: ViewState = {
    connection: 'connecting',
    sessionState: 'LOADING',
    simTime: 0,
    actualRtf: 0,
    realtimeFactor: 1,
    pending: [],
    entities: {},
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
  private commandTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private rates: number[] = [];
  private generation = 0;
  private receivedFrames = 0;
  private payloadBytes = 0;
  private publishSample?: { frames: number; time: number; revision: unknown };
  private frameFormat: 'json' | 'binary' = 'json';
  private requestedScope: SubscriptionOptions = { scope: 'all', delta: true };
  private acceptedScope: SubscriptionOptions = { scope: 'all', delta: true };
  private pendingSubscription?: string;
  private staticVisible = new Set<string>();
  private bodyIds = new Map<string, number>();
  private resyncCount = 0;

  constructor(
    readonly sessionId: string,
    readonly format: 'json' | 'binary' = 'json',
  ) {}
  getSnapshot = () => this.state;
  getStreamMetrics = () => ({
    timeMs: performance.now(),
    receivedFrames: this.receivedFrames,
    payloadBytes: this.payloadBytes,
    skippedNonFinite: this.buffer.skippedNonFinite,
    discardedFrames: this.buffer.discardedFrames,
    invalidBinaryFrames: this.buffer.invalidBinaryFrames,
    frameFormat: this.frameFormat,
    revision: this.buffer.revision,
    epoch: this.buffer.epoch,
    seq: this.buffer.lastSeq,
    sessionState: this.state.sessionState,
    connection: this.state.connection,
    socketReadyState: this.ws?.readyState ?? WebSocket.CLOSED,
  });
  getSubscriptionMetrics = () => ({
    subscriptionId: this.buffer.subscriptionId,
    scope: this.acceptedScope.scope,
    requestedScope: this.requestedScope.scope,
    delta: this.acceptedScope.delta ?? false,
    memberIds: this.buffer.memberIds,
    membershipRevision: this.buffer.membershipRevision,
    baseSeq: this.buffer.baseSeq,
    resyncPending:
      Boolean(this.pendingSubscription) || this.buffer.awaitingSnapshot,
    resyncCount: this.resyncCount,
  });
  setSubscription(options: SubscriptionOptions) {
    this.requestedScope = { ...options, delta: options.delta ?? true };
    this.subscribeStream();
  }
  usesViewport() {
    return this.requestedScope.scope === 'viewport';
  }
  updateViewport(planes: [number, number, number, number][]) {
    if (this.requestedScope.scope !== 'viewport' || planes.length !== 6) return;
    this.requestedScope = {
      ...this.requestedScope,
      viewport: {
        planes: planes as NonNullable<Subscribe['viewport']>['planes'],
      },
    };
    this.subscribeStream();
  }
  isEntityVisible(entityKey: string): boolean {
    const manifest = this.state.manifest;
    if (!manifest) return false;
    const streamId = this.bodyIds.get(entityKey);
    if (streamId !== undefined) return this.buffer.isMember(streamId);
    if (
      this.acceptedScope.scope === 'all' ||
      this.acceptedScope.scope === 'viewport'
    )
      return true;
    return this.staticVisible.has(entityKey);
  }
  private resolveStaticVisibility() {
    const manifest = this.state.manifest;
    this.staticVisible.clear();
    if (!manifest) return;
    const requested =
      this.acceptedScope.scope === 'device'
        ? [this.acceptedScope.deviceKey!]
        : (this.acceptedScope.entityKeys ?? []);
    for (const key of requested) this.staticVisible.add(key);
    // Manifest nodes are not required to be topologically sorted.
    const children = new Map<string, string[]>();
    for (const node of manifest.nodes) {
      if (node.parentEntityKey) {
        const list = children.get(node.parentEntityKey) ?? [];
        list.push(node.entityKey);
        children.set(node.parentEntityKey, list);
      }
    }
    const pending = [...requested];
    while (pending.length) {
      for (const child of children.get(pending.pop()!) ?? []) {
        if (!this.staticVisible.has(child)) {
          this.staticVisible.add(child);
          pending.push(child);
        }
      }
    }
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private update(patch: Partial<ViewState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  async start() {
    const generation = ++this.generation;
    this.stopped = false;
    document.addEventListener('visibilitychange', this.onVisibility);
    await this.loadManifest();
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
      });
    }, 250);
    this.statsPoll = setInterval(() => void this.loadStats(), 2000);
    void this.loadStats();
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
    this.bodyIds = new Map(
      manifest.nodes
        .filter((node) => node.kind === 'body')
        .map((node) => [node.entityKey, node.streamId]),
    );
    this.buffer.configure(manifest);
    this.update({ manifest, entities: {}, error: undefined });
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
        this.update({ stats, publishedRateHz });
      }
    } catch {
      /* P0 gateways may not expose the optional P1 diagnostics yet. */
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
    ws.onopen = () => {
      this.lastMessageAt = performance.now();
    };
    ws.onmessage = (event) => {
      this.lastMessageAt = performance.now();
      this.payloadBytes +=
        typeof event.data === 'string'
          ? new TextEncoder().encode(event.data).length
          : event.data.byteLength;
      messages = messages
        .then(async () => {
          if (this.stopped || this.ws !== ws) return;
          if (event.data instanceof ArrayBuffer) {
            if (
              this.buffer.pushBinary(
                event.data,
                performance.now(),
                Boolean(this.pendingSubscription),
              )
            ) {
              this.receivedFrames++;
              this.rates.push(performance.now());
            }
            this.recoverUnknownBase();
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
    if (
      this.requestedScope.scope === 'viewport' &&
      !this.requestedScope.viewport
    )
      return;
    this.buffer.expectSnapshot();
    this.pendingSubscription = crypto.randomUUID();
    this.ws.send(
      JSON.stringify({
        type: 'client.subscribe',
        protocolVersion: 1,
        sceneRevision: this.state.manifest.sceneRevision,
        ...this.requestedScope,
        requestId: this.pendingSubscription,
        rateHz: this.state.manifest.source.basePublishHz,
        format: this.format,
      }),
    );
  }

  private recoverUnknownBase() {
    if (this.buffer.needsResync && !this.pendingSubscription) {
      this.resyncCount++;
      this.subscribeStream();
    }
  }
  private async receive(message: ServerMessage, generation: number) {
    switch (message.type) {
      case 'server.ready':
        if (this.buffer.revision !== message.sceneRevision)
          await this.loadManifest();
        if (this.stopped || generation !== this.generation) return;
        this.heartbeatIntervalMs = message.heartbeatIntervalMs;
        this.buffer.paused = message.sessionState === 'PAUSED';
        this.update({
          connection: 'connected',
          sessionState: message.sessionState,
          simTime: message.simTime,
          realtimeFactor: message.realtimeFactor,
        });
        this.reconnectDelay = 500;
        this.subscribeStream();
        break;
      case 'subscription.ack':
        if (message.requestId !== this.pendingSubscription) break;
        this.pendingSubscription = undefined;
        this.acceptedScope = { ...this.requestedScope };
        this.resolveStaticVisibility();
        this.buffer.acceptSubscription(message.subscriptionId ?? undefined);
        this.buffer.grantedRateHz = message.grantedRateHz;
        this.frameFormat = message.format ?? 'json';
        break;
      case 'transform.frame':
      case 'stream.snapshot':
      case 'transform.delta':
        // Older reliable batches remain FIFO on the wire. Only the latest
        // requested ack can open the new scope's snapshot barrier.
        if (this.pendingSubscription && message.subscriptionId !== undefined)
          break;
        if (this.buffer.push(message, performance.now())) {
          this.receivedFrames++;
          this.rates.push(performance.now());
        }
        this.recoverUnknownBase();
        break;
      case 'session.state':
        if (message.sceneRevision !== this.buffer.revision) break;
        this.buffer.paused = message.state === 'PAUSED';
        if (this.buffer.paused) this.buffer.applyLatest();
        this.update({
          sessionState: message.state,
          simTime: message.simTime,
          actualRtf: message.actualRtf,
          realtimeFactor: message.realtimeFactor,
          ...(message.state === 'FAILED'
            ? {
                error:
                  message.cause.message ??
                  message.cause.code ??
                  'Source failed. Restart the session to recover.',
              }
            : {}),
        });
        break;
      case 'server.heartbeat':
        this.buffer.paused = message.state === 'PAUSED';
        this.update({
          sessionState: message.state,
          actualRtf: message.actualRtf,
        });
        break;
      case 'scene.replaced':
        this.pendingSubscription = undefined;
        this.buffer.revision = message.sceneRevision;
        this.buffer.expectSnapshot();
        this.update({
          manifest: undefined,
          entities: {},
          sessionState: 'LOADING',
        });
        await this.loadManifest();
        if (this.stopped || generation !== this.generation) return;
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
      case 'entity.state': {
        const current = this.state.entities[message.entityKey];
        if (
          message.sceneRevision === this.buffer.revision &&
          (!current || message.stateSeq > current.stateSeq)
        )
          this.update({
            entities: { ...this.state.entities, [message.entityKey]: message },
          });
        break;
      }
      case 'server.error':
        this.update({ error: `${message.code}: ${message.message}` });
        break;
    }
  }

  command(
    command: CommandRequest['command'],
    args: CommandRequest['args'] = {},
  ) {
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
