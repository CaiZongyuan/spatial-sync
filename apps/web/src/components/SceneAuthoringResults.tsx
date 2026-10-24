import { Link } from '@tanstack/react-router';
import { useEffect, useMemo, useState } from 'react';
import { getAuthoringReport, getAuthoringReports, type AuthoringReport } from '../lib/authoring';
import type { SceneDetail } from '../lib/library';

function formatTime(value: string) {
  return new Date(value).toLocaleString();
}

export function failureSummary(scene: SceneDetail) {
  const failed = (scene.validation?.checks ?? []).filter(check => check.status === 'fail');
  const lines = [`场景版本：${scene.id}`, `内容摘要：${scene.contentHash}`];
  if (failed.length) {
    lines.push('平台验证失败项：');
    for (const check of failed) {
      lines.push(`- ${check.id}${check.entityKey ? `（${check.entityKey}）` : ''}：${check.message}`);
    }
  } else {
    lines.push(`平台结论：${scene.launchReason ?? '未记录失败检查'}`);
    lines.push('- 未记录失败检查（缺少报告或报告不可启动）');
  }
  lines.push('请基于上面的精确版本与失败项在原对话中修改；平台不会据此自动发送任何消息。');
  return lines.join('\n');
}

export function CopyFailureSummary({ scene }: { scene: SceneDetail }) {
  const summary = useMemo(() => failureSummary(scene), [scene]);
  const [state, setState] = useState<'idle' | 'copied' | 'fallback'>('idle');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(summary);
      setState('copied');
    } catch {
      setState('fallback');
    }
  };
  return <div className="scene-copy-summary">
    <button type="button" className="scene-copy-button" data-testid="copy-failure-summary" onClick={() => void copy()}>
      {state === 'copied' ? '已复制' : '复制失败摘要'}
    </button>
    {state === 'copied' && <span className="scene-note" role="status">已复制精确版本与失败检查；不会自动发送消息。</span>}
    {state === 'fallback' && <div className="scene-copy-fallback"><p>无法访问剪贴板，请手动选择下面的摘要：</p><pre>{summary}</pre></div>}
  </div>;
}

export function SceneVersions({ scene }: { scene: SceneDetail }) {
  return <section className="reference-panel" aria-label="场景版本">
    <header><h2>版本</h2><span className="scene-note">{scene.versions.length} 个已接收版本</span></header>
    <div className="scene-panel-body scene-version-list" data-testid="scene-versions">
      {scene.versions.map(version => <Link key={version.id} to="/scenes/$name/$rev" params={{ name: scene.sceneId, rev: String(version.rev) }}
        className={version.id === scene.id ? 'is-current' : undefined} aria-current={version.id === scene.id ? 'page' : undefined}>
        <span><code>{version.id}</code>{version.id === scene.id && <em>当前</em>}</span>
        <small className={version.launchable ? '' : 'is-blocked'}>{version.validationStatus === 'missing' ? '缺少验证报告 · 待验证' : version.launchable ? '可启动' : '不可启动'}</small>
        <small><code>{version.contentHash.slice(0, 12)}…</code></small>
      </Link>)}
    </div>
  </section>;
}

const kinds = new Set(['robot', 'device', 'labware', 'environment']);

export function AssetDependencies({ scene }: { scene: SceneDetail }) {
  return <section className="reference-panel" aria-label="固定素材依赖">
    <header><h2>固定素材依赖</h2><span className="scene-note">{scene.assets.length}</span></header>
    <div className="scene-panel-body scene-dependencies" data-testid="scene-dependencies">
      {scene.assets.length ? scene.assets.map(asset => {
        const [bare, rev] = asset.id.split('@');
        const slash = bare.indexOf('/');
        const kind = slash < 0 ? bare : bare.slice(0, slash);
        const name = slash < 0 ? '' : bare.slice(slash + 1);
        return <div key={asset.id}>
          {kinds.has(kind) && name && rev
            ? <Link to="/assets/$kind/$name/$rev" params={{ kind, name, rev }}><code>{asset.id}</code></Link>
            : <code>{asset.id}</code>}
          <small title={asset.contentHash}><code>{asset.contentHash.slice(0, 12)}…</code></small>
        </div>;
      }) : <p className="scene-note">此版本的验证报告没有记录固定素材依赖。</p>}
    </div>
  </section>;
}

export function SceneAuthoringReports({ sceneId }: { sceneId: string }) {
  const [reports, setReports] = useState<AuthoringReport[]>();
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setReports(undefined); setError(undefined);
    void getAuthoringReports({ scene: sceneId }, controller.signal)
      .then(async listing => Promise.all(listing.reports.map(item => getAuthoringReport(item.submissionId, controller.signal))))
      .then(values => { if (!controller.signal.aborted) setReports(values); })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [sceneId, attempt]);
  return <section className="reference-panel" aria-label="外部制作摘要" data-testid="authoring-reports">
    <header><h2>外部制作摘要</h2><span className="scene-note">只读 · 不覆盖平台验证</span></header>
    <div className="scene-panel-body scene-authoring">
      {error ? <div role="alert" className="scene-failure">{error} <button onClick={() => setAttempt(attempt + 1)}>重试</button></div>
        : !reports ? <p className="scene-note" role="status">正在读取已接收的外部汇报…</p>
          : reports.length === 0 ? <p className="scene-note" data-testid="authoring-empty">未收到更新。外部对话中的制作进度不会自动同步；平台不推断未提交的 Blender 编辑。</p>
            : reports.map(report => <article key={report.submissionId} className="authoring-report" data-testid="authoring-report">
              <header>
                <div><strong>{report.source}</strong><code>{report.sourceVersion}</code></div>
                <span className="reference-tag">外部汇报</span>
              </header>
              <dl className="reference-kv">
                <div><dt>提交 ID</dt><dd><code>{report.submissionId}</code></dd></div>
                <div><dt>报告时间</dt><dd><time dateTime={report.reportedAt}>{formatTime(report.reportedAt)}</time></dd></div>
                <div><dt>接收时间</dt><dd><time dateTime={report.receivedAt}>{formatTime(report.receivedAt)}</time></dd></div>
                {report.draftId && <div><dt>外部草稿</dt><dd><code>{report.draftId}</code></dd></div>}
                {report.object && <div><dt>绑定版本</dt><dd><code>{report.object.id}</code> <small title={report.object.contentHash}>{report.object.contentHash.slice(0, 12)}…</small></dd></div>}
              </dl>
              <pre className="authoring-summary">{report.summary || '（无摘要）'}</pre>
              {report.gaps.length > 0 && <div className="authoring-gaps"><h3>已知缺口</h3><ul>{report.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></div>}
              {report.artifacts.length > 0 && <div className="authoring-artifacts"><h3>产物引用</h3><ul>{report.artifacts.map((artifact, index) => <li key={index}><span>{artifact.name}</span><code>{artifact.reference}</code></li>)}</ul></div>}
            </article>)}
    </div>
  </section>;
}
