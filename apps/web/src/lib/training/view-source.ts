import type { JsonValue, SceneManifest, TransformFrame } from '../protocol';
import { SessionClient } from '../stream/client';
import { parseTrainingViewMessage, trainingStreamIdentity, type TrainingHelloPayload, type TrainingViewMessage } from './view-protocol';

export type TrainingViewPhase = 'waiting' | 'live' | 'stale' | 'ended' | 'error';
export interface TrainingViewStatus { state: TrainingViewPhase; hello?: TrainingHelloPayload; error?: string; message?: string }
export interface TrainingViewState extends TrainingViewStatus {
  viewId?: string;
  sources: ReadonlyMap<number, TrainingEnvSource>;
}
const stable = (value: unknown) => JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
function ensure(condition: unknown, message: string): asserts condition { if (!condition) throw new Error(message); }

/** Uses only the existing pose reducers; it never joins or requests a gateway Session. */
export class TrainingEnvSource extends SessionClient {
  override readonly readOnly = true;
  hasSnapshot = false;
  lastFrameAt = 0;
  private manifestIdentity: string;
  private transportState: number = WebSocket.OPEN;

  constructor(readonly jobId: string, readonly viewId: string, readonly envIndex: number,
    manifest: SceneManifest, readonly hello: TrainingHelloPayload) {
    super(trainingStreamIdentity(viewId, envIndex));
    this.verify(manifest);
    this.manifestIdentity = stable({ ...manifest, epoch: 0 });
    this.configureManifest(manifest);
    this.buffer.grantedRateHz = hello.maxRateHz;
    this.update({ role: 'viewer', sessionState: 'TRAINING_WAITING', connection: 'connecting', realtimeFactor: null });
  }

  private verify(manifest: SceneManifest) {
    ensure(manifest.sessionId === this.sessionId && manifest.sceneRevision === `training-scene-${this.hello.descriptorDigest}`
      && manifest.revisionNumericId === 1, '训练 Manifest 与视图描述不匹配');
    ensure(stable(manifest.source) === stable(this.hello.source), '训练 Manifest 的实际 Source 不匹配');
    const prefix = `/api/v1/jobs/${encodeURIComponent(this.jobId)}/view-assets/`;
    for (const node of manifest.nodes) {
      if (node.kind === 'group') continue;
      for (const visual of [...node.visuals, ...(node.fallbackVisuals ?? []), ...node.colliders]) {
        if (visual.kind === 'primitive') continue;
        const url = new URL(visual.url, location.href);
        ensure(url.origin === location.origin && url.pathname.startsWith(prefix), '训练素材必须使用作业自有地址');
      }
    }
  }
  verifyRepeatedManifest(manifest: SceneManifest) {
    this.verify(manifest);
    ensure(stable({ ...manifest, epoch: 0 }) === this.manifestIdentity, '同一视图的几何描述发生变化');
  }
  accept(frame: TransformFrame, received = performance.now(), wireBytes = 0) {
    const manifest = this.getSnapshot().manifest!;
    ensure(frame.sessionId === this.sessionId && frame.sceneRevision === manifest.sceneRevision, '训练空间帧身份不匹配');
    const ids = new Set(manifest.nodes.flatMap(node => node.kind === 'body' ? [node.streamId] : []));
    ensure(frame.poses.length === ids.size && frame.poses.every(row => ids.has(row[0])), '训练样本缺少实际刚体或包含未知身份');
    if (!this.buffer.push(frame, received)) return false;
    this.hasSnapshot ||= frame.type === 'stream.snapshot';
    this.lastFrameAt = received;
    this.buffer.paused = false;
    this.update({ connection: 'connected', sessionState: 'TRAINING', simTime: frame.simTime,
      receivedFrames: this.getSnapshot().receivedFrames + 1, payloadBytes: this.getSnapshot().payloadBytes + wireBytes,
      publishedRateHz: this.hello.maxRateHz, error: undefined });
    return true;
  }
  freeze(reconnecting = false) {
    this.buffer.applyLatest();
    this.buffer.paused = true;
    if (reconnecting) { this.buffer.expectSnapshot(); this.transportState = WebSocket.CLOSED; }
    this.update({ connection: 'disconnected', sessionState: 'TRAINING_STALE' });
  }
  override async start() { /* The owning Job transport supplies data. */ }
  transportOpened() { this.transportState = WebSocket.OPEN; }
  override getStreamMetrics = () => ({
    timeMs: performance.now(), receivedFrames: this.getSnapshot().receivedFrames, payloadBytes: this.getSnapshot().payloadBytes,
    skippedNonFinite: this.buffer.skippedNonFinite, discardedFrames: this.buffer.discardedFrames,
    invalidBinaryFrames: this.buffer.invalidBinaryFrames, frameFormat: 'json', revision: this.buffer.revision,
    epoch: this.buffer.epoch, seq: this.buffer.lastSeq, sessionState: this.getSnapshot().sessionState,
    connection: this.getSnapshot().connection,
    socketReadyState: this.transportState,
  });
  override stop() {
    this.transportState = WebSocket.CLOSED;
    this.buffer.paused = true; this.info.dispose();
    this.update({ connection: 'disconnected', sessionState: 'TRAINING_ENDED' });
  }
  override action(_entityKey: string, _action: string, _params: Record<string, JsonValue>): never {
    throw new Error('训练视图只读');
  }
}

/** One Job socket owns all selected environments and their reconnect/disposal lifecycle. */
export class TrainingViewClient {
  private state: TrainingViewState = { state: 'waiting', sources: new Map() };
  private sources = new Map<number, TrainingEnvSource>();
  private listeners = new Set<() => void>();
  private socket?: WebSocket;
  private retry?: ReturnType<typeof setTimeout>;
  private monitor?: ReturnType<typeof setInterval>;
  private stopped = true;
  private terminal = false;
  private reconnectDelay = 500;
  private identity?: { attemptId: string; viewId: string };
  private attemptId?: string;
  private pinnedInputs?: string;
  constructor(readonly jobId: string) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(patch: Partial<TrainingViewState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach(listener => listener());
  }
  start() {
    if (!this.stopped) return;
    this.stopped = false; this.terminal = false;
    this.connect();
    this.monitor = setInterval(() => {
      if (this.state.state !== 'live') return;
      for (const source of this.sources.values()) {
        if (performance.now() - source.lastFrameAt > 3000) {
          source.freeze(); this.update({ state: 'stale', message: '暂未收到新的位姿，显示最近采样' });
        }
      }
    }, 1000);
  }
  stop() {
    this.stopped = true;
    clearTimeout(this.retry); clearInterval(this.monitor);
    this.socket?.close(); this.socket = undefined;
    this.disposeSources();
  }
  private disposeSources() {
    this.sources.forEach(source => source.stop()); this.sources.clear();
    this.update({ sources: new Map() });
  }
  private connect() {
    if (this.stopped || this.terminal) return;
    const url = new URL(`/api/v1/jobs/${encodeURIComponent(this.jobId)}/view`, location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(url.href); this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket || this.stopped) return;
      this.reconnectDelay = 500;
      this.sources.forEach(source => source.transportOpened());
      this.update({ state: this.sources.size ? 'stale' : 'waiting', error: undefined, message: '等待完整训练采样' });
    };
    socket.onmessage = event => {
      if (this.socket !== socket || this.stopped || this.terminal) return;
      try {
        ensure(typeof event.data === 'string', '训练视图需要版本化 JSON 消息');
        this.accept(parseTrainingViewMessage(event.data, this.jobId), new TextEncoder().encode(event.data).byteLength);
      } catch (error) { this.fail((error as Error).message); }
    };
    socket.onerror = () => {
      if (this.socket === socket && !this.terminal) this.update({ state: 'stale', message: '训练视图连接暂不可用，正在重连' });
    };
    socket.onclose = () => {
      if (this.socket !== socket || this.stopped || this.terminal) return;
      this.socket = undefined;
      this.sources.forEach(source => source.freeze(true));
      this.update({ state: this.sources.size ? 'stale' : 'waiting', message: '连接中断，等待完整采样恢复' });
      this.retry = setTimeout(() => this.connect(), this.reconnectDelay);
      this.reconnectDelay = Math.min(5000, this.reconnectDelay * 2);
    };
  }
  private matches(message: { attemptId: string | null; viewId: string | null }) {
    return message.viewId == null || this.identity?.attemptId === message.attemptId
      && this.identity?.viewId === message.viewId;
  }
  private accept(message: TrainingViewMessage, wireBytes: number) {
    // The gateway can bind the immutable Job dispatch before any view exists.
    if (this.attemptId != null && message.attemptId != null && this.attemptId !== message.attemptId) return;
    if (message.type === 'hello') {
      this.attemptId = message.attemptId;
      const inputs = stable({ task: message.payload.task, taskContentHash: message.payload.taskContentHash,
        scene: message.payload.scene, sceneContentHash: message.payload.sceneContentHash,
        assets: [...message.payload.assets].sort((a, b) => a.id.localeCompare(b.id)) });
      ensure(this.pinnedInputs == null || this.pinnedInputs === inputs, '同一作业的固定输入发生变化');
      this.pinnedInputs = inputs;
      const sameView = this.identity?.viewId === message.viewId;
      if (sameView) {
        ensure(this.identity?.attemptId === message.attemptId && stable(this.state.hello) === stable(message.payload), '同一视图的发布描述发生变化');
      } else {
        this.disposeSources(); this.identity = { attemptId: message.attemptId, viewId: message.viewId };
      }
      this.update({ hello: message.payload, viewId: message.viewId, state: 'waiting', error: undefined });
      return;
    }
    if (message.type === 'manifest' || message.type === 'frame') ensure(this.identity, '训练空间消息早于 Hello');
    if (!this.matches(message)) return;
    if (message.attemptId != null) this.attemptId = message.attemptId;
    if (message.type === 'manifest' || message.type === 'frame') {
      const hello = this.state.hello;
      ensure(hello && this.identity && hello.environments.includes(message.envIndex), '空间消息没有对应的环境描述');
      let source = this.sources.get(message.envIndex);
      if (message.type === 'manifest') {
        if (source) source.verifyRepeatedManifest(message.payload);
        else {
          source = new TrainingEnvSource(this.jobId, message.viewId, message.envIndex, message.payload, hello);
          this.sources.set(message.envIndex, source); this.update({ sources: new Map(this.sources) });
        }
      } else {
        ensure(source, '训练空间帧早于 Manifest');
        if (!source.accept(message.payload, performance.now(), wireBytes)) return;
        const live = hello.environments.every(index => {
          const current = this.sources.get(index);
          return current?.hasSnapshot && !current.buffer.awaitingSnapshot && current.getSnapshot().connection === 'connected';
        });
        if (this.state.state !== 'live' || !live) this.update({ state: live ? 'live' : 'waiting', message: undefined });
      }
      return;
    }
    if (message.type === 'error') { this.fail(`${message.payload.code}: ${message.payload.message}`); return; }
    if (message.type === 'ended') {
      this.terminal = true; clearInterval(this.monitor); clearTimeout(this.retry);
      this.disposeSources(); this.update({ state: 'ended', message: message.payload.reason ?? undefined }); this.socket?.close();
      return;
    }
    if (message.payload.state !== 'live') this.sources.forEach(source => source.freeze());
    const live = message.payload.state === 'live' && this.state.state === 'live';
    this.update({ state: live ? 'live' : message.payload.state === 'live' ? 'waiting' : message.payload.state,
      message: message.payload.message ?? undefined });
  }
  private fail(error: string) {
    this.terminal = true; clearInterval(this.monitor); clearTimeout(this.retry);
    this.disposeSources(); this.update({ state: 'error', error, message: undefined }); this.socket?.close();
  }
}
