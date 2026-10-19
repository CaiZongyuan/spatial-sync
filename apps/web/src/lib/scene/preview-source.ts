import { SessionClient } from '../stream/client';
import { validateManifest } from '../validate-manifest';
import type { Catalog, EntitySnapshot, JsonValue } from '../protocol';
import type { ScenePreview } from '../library';

/** One frozen initial sample; it never connects to a session or advances physics. */
export class ScenePreviewSource extends SessionClient {
  override readonly readOnly = true;
  private abort = new AbortController();
  private captureGeneration = 0;

  constructor(private readonly preview: ScenePreview) { super('scene-preview'); }

  override async start() {
    const generation = ++this.captureGeneration;
    this.abort = new AbortController();
    if (this.preview.status !== 'available' || !this.preview.documents) throw new Error(this.preview.reason ?? '预览不可用');
    const documents = this.preview.documents;
    const read = async (url: string) => {
      const response = await fetch(url, { signal: this.abort.signal });
      if (!response.ok) throw new Error(`静态预览文件不可用（${response.status}）`);
      return response;
    };
    const [raw, catalog, state, spatial] = await Promise.all([
      read(documents.manifest).then(response => response.json()),
      read(documents.catalog).then(response => response.json() as Promise<Catalog>),
      read(documents.state).then(response => response.json() as Promise<EntitySnapshot>),
      read(documents.spatial).then(response => response.arrayBuffer()),
    ]);
    if (generation !== this.captureGeneration) return;
    const manifest = validateManifest(raw);
    if (manifest.sceneRevision !== catalog.sceneRevision || manifest.sceneRevision !== state.sceneRevision)
      throw new Error('静态预览文件的场景修订不一致');
    for (const node of manifest.nodes) {
      if (node.kind === 'group') continue;
      for (const visual of [...node.visuals, ...(node.fallbackVisuals ?? []), ...node.colliders])
        if (visual.kind !== 'primitive') visual.url = new URL(visual.url, new URL(documents.manifest, location.href)).href;
    }
    this.configureManifest(manifest);
    this.info.setCatalog(catalog);
    this.applyGlobalMessage(state);
    this.buffer.pushBinary(spatial, performance.now());
    if (!this.buffer.latestSample) throw new Error('静态预览缺少有效空间样本');
    this.buffer.paused = true;
    this.buffer.applyLatest();
    this.update({ connection: 'connected', role: 'viewer', sessionState: 'PREVIEW', simTime: this.preview.simTime ?? 0 });
  }

  override stop() {
    this.captureGeneration++;
    this.abort.abort();
    this.info.dispose();
  }
  override samplePoses(_now: number) { return this.buffer.sample(Infinity); }
  override action(_entityKey: string, _action: string, _params: Record<string, JsonValue>): never {
    throw new Error('静态预览不能执行动作，请先启动会话。');
  }
}
