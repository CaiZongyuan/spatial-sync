import { createFileRoute, Link, useNavigate } from '@tanstack/react-router';
import { useEffect, useState } from 'react';
import { getSceneDetail, launchScene, rootLabel, sourceLabel, type SceneDetail, type SceneSource } from '../lib/library';
import { FrozenViewport, useScenePreview } from '../components/ScenePreview';
import { LaunchStatus } from '../components/SceneStatus';
import { deviceKey } from '../lib/info/binding';
import { LibraryDeletionRequest } from '../components/LibraryDeletion';

export const Route = createFileRoute('/scenes/$name/$rev')({
  validateSearch: (value: Record<string, unknown>): { source?: SceneSource } => ({ source: value.source === 'synthetic' || value.source === 'newton' ? value.source : undefined }),
  component: SceneDetails,
});

function SceneDetails() {
  const { name, rev } = Route.useParams();
  const { source } = Route.useSearch();
  const navigate = Route.useNavigate();
  const [detail, setDetail] = useState<SceneDetail>();
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setDetail(undefined); setError(undefined);
    void getSceneDetail(name, rev, source, controller.signal).then(value => {
      if (source && !value.sources.includes(source)) void navigate({ search: { source: value.sources[0] }, replace: true });
      else setDetail(value);
    })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [name, rev, source, attempt, navigate]);
  return <main className="scene-detail-page">{error ? <div className="platform-error" role="alert">{error} <button onClick={() => setAttempt(attempt + 1)}>重试</button></div> : !detail ? <div className="scene-empty" role="status">正在读取场景版本…</div> : <Detail key={`${detail.id}:${detail.preview.source}`} scene={detail} />}</main>;
}

function Detail({ scene }: { scene: SceneDetail }) {
  const { client, state, catalog, error } = useScenePreview(scene.preview);
  const [readouts, setReadouts] = useState(true);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string>();
  const navigate = useNavigate();
  const manifest = state.manifest;
  const source = scene.preview.source;
  const physical = manifest?.nodes.filter(node => node.kind !== 'group').filter(node => !node.entityKey.startsWith('/World/Dressing/'));
  const dressing = manifest?.nodes.filter(node => node.entityKey.startsWith('/World/Dressing/'));
  const groups = manifest?.nodes.filter(node => node.kind === 'group');
  const locations = Object.entries(scene.locations);
  return <>
    <header className="scene-page-heading"><div><Link className="reference-link" to="/scenes">← 场景库</Link><h1><code>{scene.id}</code></h1><p><LaunchStatus scene={scene} />{scene.title} · {scene.description}</p></div></header>
    <div className="scene-detail-grid"><div className="scene-detail-stack">
      <section className="reference-panel scene-preview-panel"><header><h2>视口 · {scene.id}</h2><button className="readout-switch" aria-label="初始读数" aria-pressed={readouts} onClick={() => setReadouts(!readouts)}><i />读数</button></header>
        <figure><div className="scene-detail-stage">{manifest ? <FrozenViewport {...{ client, manifest, readouts }} /> : <div className="scene-empty" role="status"><span aria-hidden="true">◇</span><p>{error ?? scene.preview.reason ?? '正在读取真实初始快照…'}</p></div>}</div>
          <figcaption><strong>图 1</strong><span>等距预览 · {sourceLabel(source)}</span><span className="reference-tag">静态初始状态 · 只读</span></figcaption></figure>
        {scene.preview.status === 'available' && <p className="scene-capture-note">采集时间 {new Date(scene.preview.capturedAt!).toLocaleString()} · 仿真时间 {(scene.preview.simTime ?? 0).toFixed(3)} s。读数来自此快照，动作需启动会话。</p>}
        {error && <p role="alert" className="scene-failure">{error}</p>}
      </section>
      <section className="reference-panel"><header><h2>实体</h2><span className="scene-note">{scene.entityCount ?? '—'} 实体 · {scene.deviceCount ?? '—'} 绑定</span></header>
        {physical && catalog ? <><div className="scene-table-wrap scene-entity-table"><table><thead><tr><th>实体</th><th>类型 / 绑定</th><th>空间节点</th></tr></thead><tbody>{physical.map(node => {
          const owner = deviceKey(manifest!, catalog, node.entityKey);
          return <tr key={node.entityKey}><td><code>{node.entityKey}</code></td><td>{owner ? <><code>{catalog.entities[owner].type}</code>{owner !== node.entityKey && <small title={owner}>绑定到 {owner.split('/').at(-1)}</small>}</> : <span className="scene-note">未绑定</span>}</td><td>{node.kind === 'body' ? '动态' : '静态'}{!node.visuals.length && !node.fallbackVisuals?.length && !node.colliders.length && <small>无渲染几何</small>}</td></tr>;
        })}</tbody></table></div><details className="scene-groups"><summary>逻辑分组 · {groups?.length ?? 0}（不计入物理实体）</summary>{groups?.map(node => <code key={node.entityKey}>{node.entityKey}</code>)}</details></> : <div className="scene-panel-body"><p className="scene-note">{scene.countsReason}。以下为声明式绑定，匹配选择器尚未展开。</p><div className="scene-table-wrap"><table><thead><tr><th>实体 / 选择器</th><th>实体类型</th></tr></thead><tbody>{scene.entities.map((binding, index) => <tr key={index}><td><code>{binding.key ?? binding.match}</code></td><td><code>{binding.type}</code></td></tr>)}</tbody></table></div></div>}
      </section>
      <section className="reference-panel"><header><h2>示教位置 · 布景</h2><span className="scene-note">{locations.length} 个位置</span></header><div className="scene-panel-body">
        {locations.length ? <div className="scene-teaching">{locations.map(([name, location]) => <details key={name}><summary><code>{name}</code><span>{location.zone ?? '—'}</span></summary><dl className="reference-kv">
          <div><dt>Site</dt><dd><code>{location.site ?? '未声明'}</code></dd></div><div><dt>位置 / m</dt><dd><code>{location.p?.map(value => value.toFixed(3)).join(', ') ?? '未声明'}</code></dd></div><div><dt>接近关节值</dt><dd><code>{location.approach?.map(value => value.toFixed(3)).join(', ') ?? '未声明'}</code></dd></div><div><dt>抓取关节值</dt><dd><code>{location.grasp?.map(value => value.toFixed(3)).join(', ') ?? '未声明'}</code></dd></div></dl></details>)}</div> : <p className="scene-note">此版本未声明示教位置。</p>}
        <div className="scene-dressing"><h3>布景（仅渲染）</h3>{dressing?.length ? dressing.map(node => <code key={node.entityKey}>{node.entityKey}</code>) : <p className="scene-note">{manifest ? '此版本未声明独立的布景节点。' : '静态预览不可用，无法检查布景。'}</p>}</div>
      </div></section>
    </div><aside className="scene-detail-stack">
      <section className="reference-panel"><header><h2>操作</h2><LaunchStatus scene={scene} /></header><div className="scene-panel-body scene-launch">
        <label>物理源<select aria-label="启动物理源" value={source} onChange={event => void navigate({ to: '/scenes/$name/$rev', params: { name: scene.sceneId, rev: String(scene.rev) }, search: { source: event.target.value as SceneSource }, replace: true })}>{scene.sources.map(kind => <option key={kind} value={kind}>{sourceLabel(kind)}</option>)}</select></label>
        <button className="primary" disabled={!scene.launchable || launching} onClick={async () => {
          setLaunching(true); setLaunchError(undefined);
          try { const session = await launchScene(scene.id, source); await navigate({ to: '/sessions/$sessionId', params: { sessionId: session.sessionId } }); }
          catch (error) { setLaunchError((error as Error).message); setLaunching(false); }
        }}>{launching ? '正在启动…' : '启动会话'} <span aria-hidden="true">↗</span></button>
        <p className="scene-note">启动固定版本 <code>{scene.id}</code></p>{!scene.launchable && <p className="scene-failure">{scene.launchReason}</p>}{launchError && <p className="scene-failure" role="alert">{launchError}</p>}
      </div></section>
      <section className="reference-panel"><header><h2>验证报告</h2><span className="scene-note">{scene.validationStatus === 'stale' ? '工具版本已变化' : scene.validationStatus === 'missing' ? '报告缺失' : '已记录'}</span></header>
        {scene.validation ? <div className="scene-validation">{['structural', 'physics'].map(tier => <div key={tier}><h3>{tier === 'structural' ? '结构检查' : '物理检查'}</h3>{scene.validation!.checks.filter(check => check.tier === tier).length ? scene.validation!.checks.filter(check => check.tier === tier).map(check => <div key={check.id} className={`scene-check is-${check.status}`}><strong>{check.id}<span>{check.status === 'pass' ? '通过' : check.status === 'warn' ? '警告' : '失败'}</span></strong><p>{check.message}</p></div>) : <p className="scene-note">未记录此类检查。</p>}</div>)}<small>{scene.validation.ranAt ? `执行于 ${new Date(scene.validation.ranAt).toLocaleString()}` : '未记录执行时间'}</small></div> : <p className="scene-panel-body scene-failure">缺少验证报告，无法启动此版本。</p>}
      </section>
      <section className="reference-panel"><header><h2>属性</h2></header><div className="scene-panel-body"><dl className="reference-kv">
        <div><dt>场景</dt><dd><code>{scene.id}</code></dd></div><div><dt>物理源</dt><dd>{sourceLabel(source)}</dd></div><div><dt>构建方式</dt><dd title={scene.metadata.stage}>{scene.metadata.stage.startsWith('python:') ? 'Python 构建器' : scene.metadata.stage}</dd></div><div><dt>来源</dt><dd>{rootLabel(scene.root)}</dd></div><div><dt>求解器</dt><dd>{scene.metadata.simulation?.solver ?? '未声明'}</dd></div><div><dt>预热</dt><dd>{scene.metadata.simulation?.warmupSteps == null ? '未声明' : `${scene.metadata.simulation.warmupSteps} 步`}</dd></div><div><dt>尺寸</dt><dd>{scene.preview.bounds ? scene.preview.bounds.max.map((max, index) => `${(max - scene.preview.bounds!.min[index]).toFixed(2)}`).join(' × ') + ' m' : '未测量'}</dd></div>
        <div><dt>内容摘要</dt><dd><code className="scene-content-hash" title={scene.contentHash}>{scene.contentHash.slice(0, 12)}…</code></dd></div></dl></div>
      </section>
      <section className="reference-panel"><header><h2>相关录制</h2><span className="scene-note">{scene.recordings.length}</span></header><div className="scene-panel-body scene-related-recordings">{scene.recordings.length ? scene.recordings.map(recording => <Link key={recording.sessionId} to="/replay/$recordingId" params={{ recordingId: recording.sessionId }} search={{ src: undefined }}>
        <code>{recording.sessionId}</code><span>{new Date(recording.createdAt).toLocaleString()} · {recording.status === 'recording' ? '录制中' : `${((recording.durationMs ?? 0) / 1000).toFixed(1)} s`}</span><small className={recording.sceneContentHash && recording.sceneContentHash !== scene.contentHash ? 'scene-failure' : 'scene-note'}>{recording.sceneContentHash === scene.contentHash ? '内容摘要一致' : recording.sceneContentHash ? '同版本名，内容摘要不同' : '旧版录制 · 内容摘要未知'}</small></Link>) : <p className="scene-note">此版本还没有录制。会话会自动记录。</p>}</div></section>
      <LibraryDeletionRequest resource="scene" id={scene.id} root={scene.root} />
    </aside></div>
  </>;
}
