import { useEffect, useRef, useState, type PointerEvent } from 'react';
import { Euler, Quaternion } from 'three';
import { getAssetLibrary, type AssetVersion } from '../lib/asset-library';
import { getSceneDraft, saveSceneDraft, type Position, type SceneDraft } from '../lib/scene-drafts';

const radians = Math.PI / 180;
const emptyDraft = (): SceneDraft => ({
  schema: 'lw.scene-draft/1', name: '', title: '', assets: [], waypoints: {},
});
type Selection = { kind: 'asset' | 'waypoint'; name: string };

function uniqueName(prefix: string, names: string[]) {
  let index = 1;
  while (names.includes(`${prefix}_${index}`)) index++;
  return `${prefix}_${index}`;
}

function NumberField({ label, value, change }: { label: string; value: number; change: (value: number) => void }) {
  return <label>{label}<input key={value} type="number" step="any" defaultValue={Number(value.toFixed(6))}
    onBlur={event => {
      const next = event.currentTarget.valueAsNumber;
      if (Number.isFinite(next)) change(next);
      else event.currentTarget.value = String(value);
    }} /></label>;
}

export default function SceneDraftEditor({ id, onSaved }: { id: string; onSaved: (id: string) => void }) {
  const [draft, setDraft] = useState<SceneDraft | undefined>(() => id === 'new' ? emptyDraft() : undefined);
  const [assets, setAssets] = useState<AssetVersion[]>([]);
  const [assetId, setAssetId] = useState('');
  const [selection, select] = useState<Selection>();
  const drag = useRef<{ selection: Selection; x: number; y: number; offset: Position }>(undefined);
  const ground = useRef<HTMLDivElement>(null);
  const [placing, setPlacing] = useState(false);
  const [radius, setRadius] = useState(5);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    const fail = (failure: Error) => { if (!controller.signal.aborted) setError(failure.message); };
    void getAssetLibrary({}, controller.signal).then(value => {
      if (!controller.signal.aborted) setAssets(value.assets);
    }).catch(fail);
    if (id !== 'new') void getSceneDraft(id, controller.signal).then(value => {
      if (!controller.signal.aborted) setDraft(value.draft);
    }).catch(fail);
    return () => controller.abort();
  }, [id]);

  if (!draft) return <p role={error ? 'alert' : 'status'}>{error || '正在读取场景草稿…'}</p>;
  const asset = selection?.kind === 'asset' ? draft.assets.find(row => row.name === selection.name) : undefined;
  const waypoint = selection?.kind === 'waypoint' ? draft.waypoints[selection.name] : undefined;
  const position = asset?.position ?? waypoint?.p;
  const assetAngles = asset && new Euler().setFromQuaternion(new Quaternion(...asset.orientation), 'ZYX');

  function move(item: Selection, position: Position) {
    setDraft(current => current && (item.kind === 'asset'
      ? { ...current, assets: current.assets.map(row => row.name === item.name ? { ...row, position } : row) }
      : { ...current, waypoints: { ...current.waypoints, [item.name]: { ...current.waypoints[item.name], p: position } } }));
  }

  function groundPoint(event: PointerEvent): Position {
    const bounds = ground.current!.getBoundingClientRect();
    return [((event.clientX - bounds.left) / bounds.width * 2 - 1) * radius,
      (1 - (event.clientY - bounds.top) / bounds.height * 2) * radius, 0];
  }

  function addWaypoint(point: Position) {
    if (!draft) return;
    const name = uniqueName('Waypoint', Object.keys(draft.waypoints));
    setDraft({ ...draft, waypoints: { ...draft.waypoints, [name]: { p: point, yaw: 0 } } });
    select({ kind: 'waypoint', name });
    setPlacing(false);
  }

  function marker(item: Selection, position: Position, yaw: number) {
    if (Math.abs(position[0]) > radius || Math.abs(position[1]) > radius) return null;
    return <button key={`${item.kind}:${item.name}`} type="button" className={`draft-marker ${item.kind}`}
      aria-label={`${item.kind === 'asset' ? '资产' : 'Waypoint'} ${item.name}`}
      aria-pressed={selection?.kind === item.kind && selection.name === item.name}
      style={{ left: `${50 + position[0] / radius * 50}%`, top: `${50 - position[1] / radius * 50}%` }}
      onClick={() => select(item)} onPointerDown={event => {
        if (event.button !== 0 || saving) return;
        event.stopPropagation();
        const [x, y] = groundPoint(event);
        drag.current = { selection: item, x: event.clientX, y: event.clientY,
          offset: [position[0] - x, position[1] - y, position[2]] };
        event.currentTarget.setPointerCapture(event.pointerId);
        select(item);
      }} onPointerMove={event => {
        const active = drag.current;
        if (!active || !event.currentTarget.hasPointerCapture(event.pointerId) ||
            Math.hypot(event.clientX - active.x, event.clientY - active.y) < 3) return;
        const [x, y] = groundPoint(event);
        const bounded = (value: number) => Math.max(-radius, Math.min(radius, Number(value.toFixed(3))));
        move(active.selection, [bounded(x + active.offset[0]), bounded(y + active.offset[1]), active.offset[2]]);
      }} onLostPointerCapture={() => { drag.current = undefined; }}>
      <span aria-hidden="true" style={{ transform: `rotate(${-yaw}rad)` }}>→</span>{item.name}
    </button>;
  }

  return <form className="resource-panel scene-draft-editor" onSubmit={async event => {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setError('');
    try { onSaved((await saveSceneDraft(draft)).id); }
    catch (failure) { setError((failure as Error).message); }
    finally { setSaving(false); }
  }}>
    <div className="resource-panel-body">
      <h2>场景草稿</h2>
      {id !== 'new' && <p role="status">已保存版本 <code>{id}</code></p>}
      <fieldset disabled={saving}>
        <div className="workbench-fields">
          <label>场景名称<input required pattern="[a-z0-9]+(\-[a-z0-9]+)*" maxLength={100} value={draft.name}
            onChange={event => setDraft({ ...draft, name: event.target.value })} /></label>
          <label>标题<input required maxLength={200} value={draft.title}
            onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
          <label>素材版本<select value={assetId} onChange={event => setAssetId(event.target.value)}>
            <option value="">选择精确版本</option>
            {assets.map(row => <option key={row.id} value={row.id}>{row.assetVersion} · {row.title}</option>)}
          </select></label>
          <button type="button" disabled={!assetId || draft.assets.length >= 64} onClick={() => {
            const source = assets.find(row => row.id === assetId);
            if (!source) return;
            const name = uniqueName('Asset', [...draft.assets, ...(draft.objects ?? [])].map(row => row.name));
            setDraft({ ...draft, assets: [...draft.assets, { name, asset: source.assetVersion,
              contentHash: source.contentHash, position: [0, 0, 0], orientation: [0, 0, 0, 1] }] });
            select({ kind: 'asset', name });
          }}>添加素材</button>
        </div>
        <div className="scene-draft-layout">
          <div>
            <div className="workbench-fields">
              <button type="button" aria-pressed={placing} disabled={Object.keys(draft.waypoints).length >= 128}
                onClick={() => setPlacing(!placing)}>点选地面添加 Waypoint</button>
              <label>地面范围<select value={radius} onChange={event => setRadius(Number(event.target.value))}>
                {[1, 5, 20, 100, 1000].map(value => <option key={value} value={value}>±{value} m</option>)}
              </select></label>
            </div>
            {placing && <p role="status">在地面平面点击落点。</p>}
            <div ref={ground} className="draft-ground" role="group" aria-label="场景地面平面"
              onPointerDown={event => {
                if (event.target !== event.currentTarget || !placing || event.button !== 0) return;
                const [x, y] = groundPoint(event);
                addWaypoint([Number(x.toFixed(3)), Number(y.toFixed(3)), 0]);
              }}>
              <span className="draft-axis x" aria-hidden="true">+X</span><span className="draft-axis y" aria-hidden="true">+Y</span>
              {draft.assets.map(row => marker({ kind: 'asset', name: row.name }, row.position,
                new Euler().setFromQuaternion(new Quaternion(...row.orientation), 'ZYX').z))}
              {Object.entries(draft.waypoints).map(([name, row]) => marker({ kind: 'waypoint', name }, row.p, row.yaw))}
            </div>
          </div>
          <div className="draft-inspector">
            <label>放置对象<select value={selection ? `${selection.kind}:${selection.name}` : ''} onChange={event => {
              const [kind, name] = event.target.value.split(':');
              select(name ? { kind: kind === 'asset' ? 'asset' : 'waypoint', name } : undefined);
            }}><option value="">选择对象</option>
              {draft.assets.map(row => <option key={row.name} value={`asset:${row.name}`}>资产 {row.name}</option>)}
              {Object.keys(draft.waypoints).map(name => <option key={name} value={`waypoint:${name}`}>Waypoint {name}</option>)}
            </select></label>
            <button type="button" disabled={Object.keys(draft.waypoints).length >= 128}
              onClick={() => addWaypoint([0, 0, 0])}>在原点添加 Waypoint</button>
            {asset && <p><code>{asset.asset}</code><small title={asset.contentHash}>{asset.contentHash.slice(0, 12)}</small></p>}
            {position && selection && <div className="workbench-fields">
              {(['X', 'Y', 'Z'] as const).map((axis, index) => <NumberField key={axis} label={`${axis}（m）`}
                value={position[index]} change={value => {
                  const next: Position = [...position];
                  next[index] = value;
                  move(selection, next);
                }} />)}
              <NumberField label="朝向（°）" value={(assetAngles?.z ?? waypoint?.yaw ?? 0) / radians} change={value => {
                if (asset && assetAngles) {
                  assetAngles.z = value * radians;
                  const orientation = new Quaternion().setFromEuler(assetAngles).toArray();
                  setDraft(current => current && { ...current, assets: current.assets.map(row =>
                    row.name === asset.name ? { ...row, orientation } : row) });
                } else if (waypoint) setDraft(current => current && { ...current,
                  waypoints: { ...current.waypoints, [selection.name]: { ...waypoint, yaw: value * radians } } });
              }} />
              <button type="button" onClick={() => {
                if (asset) setDraft({ ...draft, assets: draft.assets.filter(row => row.name !== asset.name) });
                else {
                  const waypoints = { ...draft.waypoints };
                  delete waypoints[selection.name];
                  setDraft({ ...draft, waypoints });
                }
                select(undefined);
              }}>从草稿移除</button>
            </div>}
          </div>
        </div>
        <button type="submit">保存场景草稿</button>
      </fieldset>
      {error && <p className="scene-failure" role="alert">{error}</p>}
    </div>
  </form>;
}
