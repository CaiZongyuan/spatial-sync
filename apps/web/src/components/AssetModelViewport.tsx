import { Component, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { Box3, Group, Matrix4, Mesh, Vector3 } from 'three';
import { loadVisualAsset, disposeObject } from '../lib/scene/visual-assets';
import { useViewerTheme, type ViewerTheme } from '../lib/scene/viewer-theme';
import { CameraRig } from './viewer/CameraRig';

class AssetRenderBoundary extends Component<{ children: ReactNode; onError: (error: Error) => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { this.props.onError(error); }
  render() { return this.state.failed ? null : this.props.children; }
}

function Model({ object, theme, capture, fitRequest }: {
  object: Group; theme: ViewerTheme; capture?: (png: string) => void; fitRequest: number;
}) {
  const invalidate = useThree(state => state.invalidate);
  useEffect(() => {
    object.traverse(child => { if (child instanceof Mesh) { child.castShadow = true; child.receiveShadow = true; } });
    invalidate();
  }, [object, invalidate]);
  const bounds = useMemo(() => {
    // Measure the asset frame even while the model belongs to the previous themed figure.
    object.updateWorldMatrix(true, true);
    const toView = new Matrix4().makeRotationX(-Math.PI / 2).multiply(object.matrixWorld.clone().invert());
    const box = new Box3();
    object.traverse(child => {
      if (!(child instanceof Mesh)) return;
      if (!child.geometry.boundingBox) child.geometry.computeBoundingBox();
      if (child.geometry.boundingBox) box.union(child.geometry.boundingBox.clone().applyMatrix4(new Matrix4().multiplyMatrices(toView, child.matrixWorld)));
    });
    return box;
  }, [object]);
  const center = bounds.getCenter(new Vector3());
  const diameter = Math.max(Number.EPSILON, bounds.getSize(new Vector3()).length());
  const frames = useRef(0);
  const captured = useRef(false);
  const fitted = useRef(false);
  useEffect(() => { invalidate(); }, [theme, invalidate]);
  useFrame(({ gl, scene, camera }) => {
    if (!capture || !fitted.current || captured.current) return;
    if (++frames.current < 3) { invalidate(); return; }
    gl.render(scene, camera);
    captured.current = true;
    capture(gl.domElement.toDataURL('image/png'));
  });
  return <>
    <CameraRig preset="isometric" bounds={() => bounds} revision={1} fitRequest={fitRequest} relativeScale
      onFit={() => { fitted.current = true; }} />
    <hemisphereLight args={[theme.sky, theme.floor, theme.hemi * Math.PI]} />
    <directionalLight position={[3, 6, 3]} intensity={theme.key * Math.PI} color={theme.sun} castShadow
      shadow-mapSize={[1024, 1024]} shadow-camera-left={-2} shadow-camera-right={2} shadow-camera-top={2} shadow-camera-bottom={-2} shadow-bias={-.0005} shadow-radius={4} />
    <group rotation={[-Math.PI / 2, 0, 0]}><primitive object={object} dispose={null} /></group>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[center.x, bounds.min.y - diameter * .003, center.z]} receiveShadow>
      <planeGeometry args={[diameter * 2, diameter * 2]} /><shadowMaterial transparent opacity={theme.ao * .45} />
    </mesh>
    {!capture && <OrbitControls makeDefault enableDamping={false} minDistance={diameter / 1000} maxDistance={diameter * 20}
      minZoom={.1} maxZoom={50} />}
  </>;
}

export function AssetModelViewport({ url, capture, captureKey }: { url: string; capture?: (png?: string) => void; captureKey?: string }) {
  const theme = useViewerTheme();
  const [loaded, setLoaded] = useState<{ url: string; object: Group; meshCount: number }>();
  const [failure, setFailure] = useState<{ url: string; message: string }>();
  const [attempt, setAttempt] = useState(0);
  const [fitRequest, setFitRequest] = useState(0);
  useEffect(() => {
    let alive = true;
    let owned: Group | undefined;
    setLoaded(undefined); setFailure(undefined);
    void loadVisualAsset({ kind: 'glb', url, assetUpAxis: 'Z', scale: [1, 1, 1], localPose: { p: [0, 0, 0], q: [0, 0, 0, 1] } }).then(object => {
      if (!object) return;
      if (!alive) { disposeObject(object); return; }
      owned = object;
      let meshCount = 0;
      object.traverse(child => { if (child instanceof Mesh) meshCount++; });
      if (!meshCount) throw new Error('视觉文件没有可显示的网格');
      setLoaded({ url, object, meshCount });
    }).catch((error: Error) => { if (alive) setFailure({ url, message: error.message }); });
    return () => { alive = false; if (owned) disposeObject(owned); };
  }, [url, attempt]);
  const error = failure?.url === url ? failure.message : undefined;
  const current = !error && loaded?.url === url ? loaded : undefined;
  useEffect(() => { if (error) capture?.(); }, [error, capture]);
  return <div className="asset-model-viewport" aria-label="素材静态模型预览" data-model-state={error ? 'error' : current ? 'ready' : 'loading'} data-mesh-count={current?.meshCount}>
    {!error && <AssetRenderBoundary key={`${url}:${attempt}`} onError={error => setFailure({ url, message: error.message })}><Canvas flat shadows frameloop="demand" dpr={[1, 1.75]} gl={{ antialias: true, alpha: true, preserveDrawingBuffer: !!capture }}>
      {current && <Model key={`${url}:${captureKey ?? ''}`} object={current.object} theme={theme} capture={capture} fitRequest={fitRequest} />}
    </Canvas></AssetRenderBoundary>}
    {current && !capture && <button type="button" className="viewport-fit" aria-label="Fit model"
      onClick={() => setFitRequest(value => value + 1)}>重新取景</button>}
    {!current && <div className="asset-preview-status" role={error ? 'alert' : 'status'}>{error ? <><span aria-hidden="true">◇</span><p>模型无法载入：{error}</p>{!capture && <button onClick={() => setAttempt(value => value + 1)}>重试预览</button>}</> : '正在读取素材模型…'}</div>}
  </div>;
}
