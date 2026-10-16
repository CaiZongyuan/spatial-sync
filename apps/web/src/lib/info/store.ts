import type {
  Catalog,
  EntityValues,
  EntitySnapshot,
  EntityObservations,
  EntityEvent,
  Invocation,
  InvocationUpdate,
} from '../protocol';

export type InfoMessage =
  | EntitySnapshot
  | EntityObservations
  | EntityEvent
  | InvocationUpdate;
type Listener = () => void;
const emptyInvocations: Invocation[] = [];
export const terminal = (invocation: Invocation) =>
  ['succeeded', 'failed', 'cancelled', 'rejected'].includes(invocation.status);

/** Live and recorded messages share revision, epoch and immutable read semantics. */
export class InfoStore {
  private revision = '';
  private epoch = -1;
  private entities = new Map<string, EntityValues>();
  private invocations = new Map<string, Invocation>();
  private invocationLists = new Map<string, Invocation[]>();
  private entityListeners = new Map<string, Set<Listener>>();
  private invocationListeners = new Map<string, Set<Listener>>();
  private metadataListeners = new Set<Listener>();
  private treeListeners = new Set<Listener>();
  private metadata: { catalog?: Catalog; ready: boolean } = { ready: false };
  private tree: Record<string, string> = {};
  private treeTimer?: ReturnType<typeof setTimeout>;

  private watch(
    listeners: Map<string, Set<Listener>>,
    key: string,
    listener: Listener,
  ) {
    const owned = listeners.get(key) ?? new Set();
    owned.add(listener);
    listeners.set(key, owned);
    return () => {
      owned.delete(listener);
      if (!owned.size) listeners.delete(key);
    };
  }
  subscribeEntity = (key: string, listener: Listener) =>
    this.watch(this.entityListeners, key, listener);
  subscribeInvocations = (key: string, listener: Listener) =>
    this.watch(this.invocationListeners, key, listener);
  subscribeMetadata = (listener: Listener) => {
    this.metadataListeners.add(listener);
    return () => {
      this.metadataListeners.delete(listener);
    };
  };
  subscribeTree = (listener: Listener) => {
    this.treeListeners.add(listener);
    return () => {
      this.treeListeners.delete(listener);
    };
  };
  getMetadata = () => this.metadata;
  getTree = () => this.tree;
  getEntity = (key: string) => this.entities.get(key);
  getInvocations = (key = '') =>
    this.invocationLists.get(key) ?? emptyInvocations;

  configure(revision: string, epoch: number) {
    this.revision = revision;
    this.epoch = epoch;
    const keys = [...this.entities.keys()];
    this.entities.clear();
    this.invocations.clear();
    this.publishInvocations();
    keys.forEach((key) => this.changed(key));
    this.metadata = { ready: false };
    this.metadataListeners.forEach((listener) => listener());
  }
  expectSnapshot() {
    this.metadata = { ...this.metadata, ready: false };
    this.metadataListeners.forEach((listener) => listener());
  }
  setCatalog(catalog: Catalog) {
    if (catalog.sceneRevision !== this.revision) return;
    this.metadata = { ...this.metadata, catalog };
    this.metadataListeners.forEach((listener) => listener());
  }
  dispose() {
    clearTimeout(this.treeTimer);
    this.treeTimer = undefined;
  }

  apply(message: InfoMessage) {
    if (message.sceneRevision !== this.revision || message.epoch < this.epoch)
      return;
    if (message.type === 'entity.snapshot') {
      this.epoch = message.epoch;
      const keys = new Set([
        ...this.entities.keys(),
        ...message.entities.map((entity) => entity.key),
      ]);
      this.entities = new Map(
        message.entities.map((entity) => [entity.key, entity]),
      );
      this.invocations = new Map(
        message.invocations.map((invocation) => [
          invocation.invocationId,
          invocation,
        ]),
      );
      this.publishInvocations();
      keys.forEach((key) => this.changed(key));
      this.metadata = { ...this.metadata, ready: true };
      this.metadataListeners.forEach((listener) => listener());
      return;
    }
    if (message.epoch !== this.epoch || !this.metadata.ready) return;
    if (message.type === 'invocation.update') {
      this.invocations.set(message.invocationId, message);
      this.publishInvocations();
    } else if (message.type === 'entity.observations') {
      for (const change of message.changes) {
        const previous = this.entities.get(change.key);
        if (previous && change.rev < previous.rev) continue;
        const meta = { ...previous?.meta };
        const values = { ...previous?.values };
        let accepted = false;
        for (const [property, value] of Object.entries(change.values)) {
          // Rate-limited properties can flush later without changing entity rev.
          if (
            change.rev === previous?.rev &&
            meta[property] &&
            message.sampleTimeMs < meta[property].sampleTimeMs
          )
            continue;
          accepted = true;
          values[property] = value;
          meta[property] = {
            simTime: message.simTime,
            sampleTimeMs: message.sampleTimeMs,
            source: message.source,
          };
        }
        if (!accepted) continue;
        this.entities.set(change.key, {
          key: change.key,
          rev: change.rev,
          values,
          meta,
        });
        this.changed(change.key);
      }
    }
    // Events describe history; current property authority comes from observations.
  }

  private publishInvocations() {
    const prior = this.invocationLists;
    this.invocationLists = new Map();
    let completed = 0;
    const all = [...this.invocations.values()]
      .sort(
        (a, b) =>
          b.createdAtMs - a.createdAtMs ||
          b.invocationId.localeCompare(a.invocationId),
      )
      .filter((invocation) => !terminal(invocation) || completed++ < 1000);
    this.invocations = new Map(
      all.map((invocation) => [invocation.invocationId, invocation]),
    );
    this.invocationLists.set('', all);
    for (const invocation of all) {
      const list = this.invocationLists.get(invocation.entityKey) ?? [];
      list.push(invocation);
      this.invocationLists.set(invocation.entityKey, list);
    }
    for (const key of new Set([
      ...prior.keys(),
      ...this.invocationLists.keys(),
    ])) {
      const before = prior.get(key) ?? emptyInvocations;
      const after = this.invocationLists.get(key) ?? emptyInvocations;
      if (
        before.length === after.length &&
        before.every((value, index) => value === after[index])
      ) {
        this.invocationLists.set(key, before);
        continue;
      }
      this.invocationListeners.get(key)?.forEach((listener) => listener());
    }
  }
  private changed(key: string) {
    this.entityListeners.get(key)?.forEach((listener) => listener());
    if (this.treeTimer !== undefined) return;
    this.treeTimer = setTimeout(() => {
      this.treeTimer = undefined;
      const next: Record<string, string> = {};
      for (const [key, entity] of this.entities)
        if (typeof entity.values.phase === 'string')
          next[key] = entity.values.phase;
      this.tree = next;
      this.treeListeners.forEach((listener) => listener());
    }, 250);
  }
}
