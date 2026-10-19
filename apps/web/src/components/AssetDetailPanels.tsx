import { Link } from '@tanstack/react-router';
import type { AssetDetail } from '../lib/asset-library';

const value = (input: unknown) => typeof input === 'string' ? input : JSON.stringify(input, null, 2);
function schemaText(schema: Record<string, unknown>) {
  if (Array.isArray(schema.enum)) return schema.enum.map(value).join(' | ');
  const type = Array.isArray(schema.type) ? schema.type.join(' | ') : schema.type ?? 'schema';
  const limits = Object.entries(schema).filter(([name]) => ['minimum', 'maximum', 'minItems', 'maxItems', 'pattern', 'format'].includes(name));
  return [type, ...limits.map(([name, input]) => `${name}: ${value(input)}`)].join(' · ');
}
export const licenseFile = (asset: AssetDetail) => Object.keys(asset.files).find(name => /^license(?:\.(?:txt|md))?$/i.test(name));

export function AssetBindings({ asset }: { asset: AssetDetail }) {
  const bindings = asset.metadata.implements ?? [];
  return <section className="asset-panel"><header><h2>绑定模板</h2></header><div className="asset-panel-body">
    {!bindings.length ? <p className="asset-small-note">此素材没有绑定模板。</p> : bindings.map((binding, index) => <div className="asset-binding" key={`${binding.type}:${index}`}>
      <h3><code>{binding.type}</code></h3><dl className="asset-programs">{Object.entries(binding.programs ?? {}).map(([source, program]) => <div key={source}><dt>{source}</dt><dd><code>{program}</code></dd></div>)}</dl>
      {Object.keys(binding.config ?? {}).length ? <div className="asset-table-wrap"><table><thead><tr><th>绑定角色</th><th>目标 / 配置</th></tr></thead><tbody>{Object.entries(binding.config ?? {}).map(([role, target]) => <tr key={role}><td><code>{role}</code></td><td><pre>{value(target)}</pre></td></tr>)}</tbody></table></div> : <p className="asset-small-note">未声明配置。</p>}
    </div>)}
  </div></section>;
}

export function AssetEntityTypes({ asset }: { asset: AssetDetail }) {
  return <>{asset.implementedTypes.map(type => {
    const definition = asset.types[type];
    return <section key={type} className="asset-panel" data-testid={`asset-type-${type}`}><header><h2>实体类型 <code>{type}</code></h2></header><div className="asset-panel-body">
      {!definition ? <p className="asset-small-note">未提供此类型的定义，请检查素材验证结果。</p> : <>
        {definition.title && <p className="asset-type-title">{definition.title}</p>}{definition.description && <p className="asset-small-note">{definition.description}</p>}
        <h3 className="asset-subheading">属性</h3><dl className="asset-type-properties">{Object.entries(definition.properties).map(([name, property]) => <div key={name}>
          <dt><code>{name}</code>{property.unit && <span className="asset-small-note">{property.unit}</span>}</dt><dd><code>{schemaText(property.schema)}</code>{property.description && <p>{property.description}</p>}<details><summary>完整属性声明</summary><pre>{JSON.stringify(property, null, 2)}</pre></details></dd>
        </div>)}</dl>
        <h3 className="asset-subheading">动作与前置条件</h3>{Object.keys(definition.actions).length ? Object.entries(definition.actions).map(([name, action]) => <details className="asset-type-action" key={name} data-testid={`asset-action-${type}-${name}`}>
          <summary><code>{name}({Object.keys(action.params ?? {}).join(', ')})</code>{action.risk === 'high' && <span className="asset-chip is-warning"><i />高风险</span>}</summary>
          {action.description && <p className="asset-small-note">{action.description}</p>}
          {Object.entries(action.params ?? {}).map(([parameter, schema]) => <div className="asset-action-parameter" key={parameter}><code>{parameter}</code>{action.required?.includes(parameter) && <span className="asset-small-note">必填</span>}<span className="asset-small-note">{schemaText(schema)}</span></div>)}
          <h4>前置条件</h4>{action.preconditions?.length ? <ol className="asset-conditions">{action.preconditions.map((condition, index) => <li key={index}><pre>{JSON.stringify(condition, null, 2)}</pre></li>)}</ol> : <p className="asset-small-note">无已声明前置条件。</p>}
          <details><summary>完整动作声明</summary><pre>{JSON.stringify(action, null, 2)}</pre></details>
        </details>) : <p className="asset-small-note">此类型未声明动作。</p>}
      </>}
    </div></section>;
  })}</>;
}

function SourceValue({ input }: { input: unknown }) {
  if (typeof input === 'string') {
    try {
      const url = new URL(input);
      if (url.protocol === 'https:' || url.protocol === 'http:') return <a href={url.href} target="_blank" rel="noreferrer">{input} ↗</a>;
    } catch { /* Non-URL provenance is displayed as text. */ }
  }
  return <span>{value(input)}</span>;
}
export function AssetProvenance({ asset }: { asset: AssetDetail }) {
  const license = licenseFile(asset);
  const original = Object.keys(asset.files).filter(name => /^(?:license(?:\.(?:txt|md))?|notice(?:\..*)?|upstream_readme\.md)$/i.test(name));
  return <section className="asset-panel"><header><h2>来源与许可证</h2></header><div className="asset-panel-body">
    {asset.provenance ? <dl className="asset-source-record">{Object.entries(asset.provenance).map(([key, input]) => <div key={key}><dt><code>{key}</code></dt><dd>{(key === 'files' || key === 'sourceFiles') && input && typeof input === 'object' ? `${Object.keys(input).length} 项文件指纹` : <SourceValue input={input} />}</dd></div>)}</dl> : <p className="asset-small-note">未提供来源记录。</p>}
    {asset.files['provenance.json'] && <a className="asset-document-link" href={asset.files['provenance.json']} target="_blank" rel="noreferrer">查看完整来源记录 ↗</a>}
    <div className="asset-license-links">{original.map(name => <a key={name} href={asset.files[name]} target="_blank" rel="noreferrer">{name} ↗</a>)}</div>
    {!license && <p className="asset-small-note">素材包没有原始许可证文件。</p>}
  </div></section>;
}

export function AssetReferencingScenes({ asset }: { asset: AssetDetail }) {
  const inspection = asset.referenceInspection;
  return <section className="asset-panel"><header><h2>引用它的场景</h2></header><div className="asset-panel-body">
    {asset.referencingScenes.length ? <ul className="asset-scene-references">{asset.referencingScenes.map(scene => <li key={scene.id}><Link to="/scenes/$name/$rev" params={{ name: scene.sceneId, rev: String(scene.rev) }} search={{}}><code>{scene.id}</code><span>{scene.title}</span></Link></li>)}</ul> : <p className="asset-small-note">{inspection.complete ? '没有已索引场景引用此版本。' : '尚未确认引用，部分场景的检查未完成。'}</p>}
    {!inspection.complete && <div className="asset-reference-warning" role="status"><p>引用检查尚不完整。</p>{inspection.reason && <p>{inspection.reason}</p>}<ul>{inspection.uninspectedScenes.map(scene => <li key={scene.id}><code>{scene.id}</code><span>{scene.reason}</span></li>)}</ul></div>}
  </div></section>;
}
