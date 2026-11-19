import { Component, lazy, Suspense, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApprovalInboxContext } from '../lib/approvals';
import type { AssetVersion } from '../lib/asset-library';
import type { SceneVersion } from '../lib/library';
import { saveThumbnail } from '../lib/thumbnails';
import { cacheThumbnailBlob } from '../lib/thumbnail-requests';

const Renderer = lazy(() => import('./ThumbnailCaptureRenderer'));

export class CaptureFailure extends Component<{ children: ReactNode; failed: () => void; resetKey?: string }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.failed(); }
  componentDidUpdate(prevProps: { resetKey?: string }) {
    if (this.state.failed && prevProps.resetKey !== this.props.resetKey) this.setState({ failed: false });
  }
  render() { return this.state.failed ? null : this.props.children; }
}

export function CaptureRendererHost({ asset, scene, done, resetKey }: {
  asset?: AssetVersion; scene?: SceneVersion; done: (png?: string) => void; resetKey?: string;
}) {
  return <div className="scene-thumbnail-renderer" aria-hidden="true"><CaptureFailure resetKey={resetKey} failed={() => done()}>
    <Suspense fallback={null}><Renderer {...{ asset, scene, done }} /></Suspense>
  </CaptureFailure></div>;
}

function supportsWebGL(): boolean {
  if (typeof document === 'undefined') return false;
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) return false;
    // Immediately release the probe context so checking never exhausts browser WebGL context limits.
    if ('getExtension' in gl) gl.getExtension('WEBGL_lose_context')?.loseContext();
    return true;
  } catch {
    return false;
  }
}

export interface ThumbnailJob<T> {
  key: string;
  path: string;
  item: T;
  capturedAt?: string;
}

export interface PreparedCapture {
  asset?: AssetVersion;
  scene?: SceneVersion;
  capturedAt?: string;
}

export function useThumbnailQueue<T>({ theme, prepare }: {
  theme: 'light' | 'dark';
  prepare: (item: T, signal: AbortSignal) => Promise<PreparedCapture | null>;
}) {
  const permissions = useContext(ApprovalInboxContext);
  const canPersist = permissions.inbox?.role === 'operator' && !permissions.error;
  const permissionDenied = Boolean(permissions.error || (permissions.inbox && permissions.inbox.role !== 'operator'));
  const prepareRef = useRef(prepare);
  prepareRef.current = prepare;

  const pendingRef = useRef<ThumbnailJob<T>[]>([]);
  const seenRef = useRef<Set<string>>(new Set());
  const [queueVersion, setQueueVersion] = useState(0);
  const [active, setActive] = useState<{
    key: string; path: string; asset?: AssetVersion; scene?: SceneVersion; capturedAt?: string;
  }>();
  const [tokens, setTokens] = useState<Record<string, number>>({});
  const busyRef = useRef(false);
  const controllerRef = useRef<AbortController | undefined>(undefined);

  useEffect(() => {
    controllerRef.current?.abort();
    for (const pending of pendingRef.current) seenRef.current.delete(pending.key);
    pendingRef.current = [];
    busyRef.current = false;
    setActive(undefined);
  }, [theme]);

  useEffect(() => {
    if (!permissionDenied) return;
    controllerRef.current?.abort();
    pendingRef.current = [];
    busyRef.current = false;
    setActive(undefined);
  }, [permissionDenied]);

  const pump = useCallback(() => {
    if (busyRef.current || !canPersist || !supportsWebGL()) return;
    const next = pendingRef.current.shift();
    if (!next) {
      setActive(undefined);
      return;
    }
    busyRef.current = true;
    const controller = new AbortController();
    controllerRef.current = controller;
    void prepareRef.current(next.item, controller.signal).then(prepared => {
      if (controller.signal.aborted) return;
      if (!prepared || (!prepared.asset && !prepared.scene)) {
        busyRef.current = false;
        if (pendingRef.current.length === 0) setActive(undefined);
        setQueueVersion(value => value + 1);
        return;
      }
      setActive({
        key: next.key, path: next.path, asset: prepared.asset,
        scene: prepared.scene, capturedAt: prepared.capturedAt ?? next.capturedAt,
      });
    }).catch(() => {
      if (controller.signal.aborted) return;
      busyRef.current = false;
      if (pendingRef.current.length === 0) setActive(undefined);
      setQueueVersion(value => value + 1);
    });
  }, [canPersist]);

  useEffect(() => {
    if (busyRef.current || !canPersist) return;
    if (pendingRef.current.length === 0) {
      setActive(undefined);
      return;
    }
    if (active) {
      pump();
      return;
    }
    // Defer the first offscreen WebGL capture briefly so initial navigation and quick theme/filter toggles stay snappy.
    const timer = setTimeout(() => {
      if (!busyRef.current && pendingRef.current.length > 0) pump();
    }, 400);
    return () => clearTimeout(timer);
  }, [active, canPersist, queueVersion, pump]);

  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(() => {
      controllerRef.current?.abort();
      busyRef.current = false;
      setActive(undefined);
      setQueueVersion(value => value + 1);
    }, 60000);
    return () => clearTimeout(timer);
  }, [active]);

  useEffect(() => () => {
    controllerRef.current?.abort();
    pendingRef.current = [];
    busyRef.current = false;
  }, []);

  const enqueue = useCallback((job: ThumbnailJob<T>) => {
    if (permissionDenied || !supportsWebGL() || seenRef.current.has(job.key)) return;
    seenRef.current.add(job.key);
    pendingRef.current.push(job);
    setQueueVersion(value => value + 1);
  }, [permissionDenied]);

  const done = useCallback((png?: string) => {
    const current = active;
    const controller = controllerRef.current;
    if (!current || !controller || controller.signal.aborted) return;
    const finish = () => {
      // Yield briefly to keep scrolling and clicks responsive between offscreen captures.
      setTimeout(() => {
        if (controller.signal.aborted) return;
        busyRef.current = false;
        if (pendingRef.current.length === 0) setActive(undefined);
        setQueueVersion(value => value + 1);
      }, 200);
    };
    if (!png) { finish(); return; }
    void saveThumbnail(current.path, png, current.capturedAt, controller.signal)
      .then(({ blob }) => {
        if (controller.signal.aborted) return;
        cacheThumbnailBlob('/api/v1' + current.path, blob);
        setTokens(prev => ({ ...prev, [current.key]: (prev[current.key] ?? 0) + 1 }));
      })
      .catch(() => undefined)
      .finally(() => { if (!controller.signal.aborted) finish(); });
  }, [active]);

  const host = useMemo(
    () => (active ? <CaptureRendererHost key={active.scene ? 'scene' : 'asset'} resetKey={active.key} asset={active.asset} scene={active.scene} done={done} /> : null),
    [active, done],
  );

  return { enqueue, tokens, host };
}
