import type { Catalog, SceneManifest } from '../protocol';
import { SessionClient } from '../stream/client';
import { validateManifest } from '../validate-manifest';
import { readRecording, readSegment, recordingUrl, type LogRecord, type Recording } from './reader';

interface PlaybackState {
  ready: boolean;
  playing: boolean;
  seeking: boolean;
  seekTargetMs?: number;
  timeMs: number;
  durationMs: number;
  rate: number;
  status?: Recording['status'];
  sceneVersion?: string | null;
  sceneContentHash?: string | null;
  kind?: Recording['kind'];
  droppedFrames: number;
}

/** A file transport feeding the live spatial/info reducers in original FIFO order. */
export class ReplaySource extends SessionClient {
  override readonly readOnly: boolean = true;
  private playback: PlaybackState = {
    ready: false, playing: false, seeking: false, timeMs: 0,
    durationMs: 0, rate: 1, droppedFrames: 0,
  };
  private playbackListeners = new Set<() => void>();
  private recording?: Recording;
  private segments = new Map<number, Promise<ReturnType<typeof readSegment>>>();
  private documents = new Map<string, Promise<{ manifest: SceneManifest; catalog: Catalog }>>();
  private records: LogRecord[] = [];
  private cursor = 0;
  private segmentIndex = 0;
  private seekVersion = 0;
  private anchor = 0;
  private anchorPosition = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private abort = new AbortController();
  private lastViewUpdate = 0;
  readonly documentUrl: string;

  constructor(recordingId: string, src?: string) {
    super(recordingId);
    this.documentUrl = new URL(src ?? `/recordings/${encodeURIComponent(recordingId)}/recording.json`, location.href).href;
  }

  getPlayback = () => this.playback;
  subscribePlayback = (listener: () => void) => {
    this.playbackListeners.add(listener);
    return () => { this.playbackListeners.delete(listener); };
  };
  private updatePlayback(patch: Partial<PlaybackState>) {
    this.playback = { ...this.playback, ...patch };
    this.playbackListeners.forEach((listener) => listener());
  }

  override async start() {
    this.abort = new AbortController();
    const version = ++this.seekVersion;
    clearTimeout(this.timer);
    this.updatePlayback({ ready: false, playing: false, seeking: false, seekTargetMs: undefined });
    const raw = await this.fetchJson<unknown>(this.documentUrl);
    if (version !== this.seekVersion) return;
    const recording = readRecording(raw);
    if (!recording.segments.length) throw new Error('This recording has no playable samples');
    this.recording = recording;
    const durationMs = Math.max(0, recording.segments.at(-1)!.endSampleTimeMs - recording.segments[0].startSampleTimeMs);
    this.updatePlayback({ durationMs, status: recording.status, sceneVersion: recording.sceneVersion,
      sceneContentHash: recording.sceneContentHash, kind: recording.kind });
    await this.seek(0);
    if (this.abort.signal.aborted) return;
    this.update({ connection: 'connected', role: 'viewer' });
  }

  override stop() {
    this.seekVersion++;
    this.abort.abort();
    clearTimeout(this.timer);
    this.updatePlayback({ ready: false, playing: false, seeking: false, seekTargetMs: undefined });
    this.segments.clear();
    this.documents.clear();
    this.info.dispose();
  }

  override samplePoses(_now: number) {
    // A recorded clock owns pacing. No live receive-clock delay while paused or seeking.
    return this.buffer.sample(Infinity);
  }

  private async fetchJson<T>(url: string): Promise<T> {
    const response = await fetch(url, { signal: this.abort.signal });
    if (!response.ok) throw new Error(`Recording unavailable (${response.status})`);
    return response.json() as Promise<T>;
  }

  private loadSegment(index: number) {
    let pending = this.segments.get(index);
    if (!pending) {
      pending = (async () => {
        const segment = this.recording!.segments[index];
        const response = await fetch(recordingUrl(segment.file, this.documentUrl), { signal: this.abort.signal });
        if (!response.ok) throw new Error(`Recording segment unavailable (${response.status})`);
        const result = readSegment(await response.arrayBuffer());
        if (result.records[0]?.kind !== 1 || result.records[1]?.message?.type !== 'entity.snapshot')
          throw new Error('Recording segment requires spatial and entity snapshots');
        return result;
      })();
      this.segments.set(index, pending);
      // Keep current/adjacent seeks bounded even for a long recording.
      for (const key of this.segments.keys())
        if (Math.abs(key - index) > 1) this.segments.delete(key);
    }
    return pending;
  }

  private loadDocuments(revision: string) {
    let pending = this.documents.get(revision);
    if (!pending) {
      const entry = this.recording!.revisions?.[revision];
      pending = Promise.all([
        this.fetchJson<unknown>(recordingUrl(entry?.manifest ?? 'manifest.json', this.documentUrl)),
        this.fetchJson<Catalog>(recordingUrl(entry?.catalog ?? 'catalog.json', this.documentUrl)),
      ]).then(([raw, catalog]) => {
        const manifest = validateManifest(raw);
        if (manifest.sceneRevision !== revision || catalog.sceneRevision !== revision)
          throw new Error('Recording documents do not match segment revision');
        for (const node of manifest.nodes) {
          if (node.kind === 'group') continue;
          for (const visual of [...node.visuals, ...(node.fallbackVisuals ?? []), ...node.colliders])
            if (visual.kind !== 'primitive') visual.url = recordingUrl(visual.url, this.documentUrl);
        }
        return { manifest, catalog };
      });
      this.documents.set(revision, pending);
    }
    return pending;
  }

  async seek(timeMs: number) {
    if (!this.recording || this.abort.signal.aborted) return;
    clearTimeout(this.timer);
    const version = ++this.seekVersion;
    const position = Math.max(0, Math.min(this.playback.durationMs, timeMs));
    const target = this.recording.segments[0].startSampleTimeMs + position;
    let index = 0;
    this.recording.segments.forEach((segment, candidate) => {
      if (segment.startSampleTimeMs <= target) index = candidate;
    });
    const revision = this.recording.segments[index].sceneRevision ?? this.recording.sceneRevision;
    this.updatePlayback({ seeking: true, seekTargetMs: position });
    try {
      const [segment, documents] = await Promise.all([this.loadSegment(index), this.loadDocuments(revision)]);
      if (version !== this.seekVersion) return;
      this.configureManifest(documents.manifest);
      this.info.setCatalog(documents.catalog);
      this.records = segment.records;
      this.segmentIndex = index;
      this.cursor = 0;
      this.updatePlayback({ droppedFrames: 0 });
      this.applyThrough(target);
      this.anchor = performance.now();
      this.anchorPosition = position;
      this.refreshView();
      this.updatePlayback({ ready: true, seeking: false, seekTargetMs: undefined, timeMs: position });
      if (this.playback.playing) this.schedule();
    } catch (error) {
      if (version !== this.seekVersion) return;
      this.updatePlayback({ seeking: false, seekTargetMs: undefined, playing: false });
      this.update({ error: (error as Error).message });
      throw error;
    }
  }

  private applyThrough(target: number) {
    while (this.cursor < this.records.length && (
      this.cursor < 2 || this.records[this.cursor].atMs <= target ||
      (this.cursor === 2 && this.records[this.cursor].message?.type === 'session.state')
    )) {
      const record = this.records[this.cursor++];
      if (record.kind === 1) this.buffer.pushBinary(record.payload, record.atMs);
      else if (record.kind === 2) this.applyGlobalMessage(record.message!);
      else this.updatePlayback({ droppedFrames: this.playback.droppedFrames + (record.gap?.count ?? 0) });
    }
    this.buffer.applyLatest();
  }

  private refreshView() {
    this.lastViewUpdate = performance.now();
    this.update({ simTime: this.buffer.displayedSimTime });
  }

  play() {
    if (!this.playback.ready || this.playback.seeking || this.playback.playing) return;
    if (this.playback.timeMs >= this.playback.durationMs) {
      this.updatePlayback({ playing: true });
      void this.seek(0).catch(() => {});
      return;
    }
    this.anchor = performance.now();
    this.anchorPosition = this.playback.timeMs;
    this.updatePlayback({ playing: true });
    this.schedule();
  }

  pause() {
    clearTimeout(this.timer);
    if (!this.playback.playing) return;
    const position = this.position();
    this.updatePlayback({ playing: false });
    void this.seek(position).catch(() => {});
  }

  setRate(rate: number) {
    if (![0.25, 0.5, 1, 2, 4].includes(rate)) throw new Error('Unsupported playback rate');
    if (this.playback.seeking) {
      this.updatePlayback({ rate });
      return;
    }
    this.anchorPosition = this.position();
    this.anchor = performance.now();
    this.updatePlayback({ rate, timeMs: this.anchorPosition });
  }

  private position() {
    return this.playback.playing
      ? Math.min(this.playback.durationMs, this.anchorPosition + (performance.now() - this.anchor) * this.playback.rate)
      : this.playback.timeMs;
  }

  private schedule() {
    this.timer = setTimeout(() => {
      if (!this.playback.playing || this.playback.seeking) return;
      const position = this.position();
      const target = this.recording!.segments[0].startSampleTimeMs + position;
      const next = this.recording!.segments[this.segmentIndex + 1];
      if (next && target >= next.startSampleTimeMs) {
        void this.seek(position).catch(() => {});
        return;
      }
      this.applyThrough(target);
      if (performance.now() - this.lastViewUpdate >= 100) {
        this.refreshView();
        this.updatePlayback({ timeMs: position });
      }
      if (position >= this.playback.durationMs) {
        this.refreshView();
        this.updatePlayback({ playing: false, timeMs: position });
      } else this.schedule();
    }, 16);
  }
}
