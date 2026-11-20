import { useState, type FormEvent } from 'react';
import { issueNodeRegistration, revokeNode, type NodeRegistration, type TrainingNode } from '../lib/jobs';
import { localTime } from '../lib/training-presentation';

export function NodeRegistrationControls({ nodes, refresh }: { nodes: TrainingNode[]; refresh: () => void }) {
  const [receipt, setReceipt] = useState<NodeRegistration>();
  const [revokeId, setRevokeId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function issue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    setReceipt(undefined);
    setBusy(true);
    setError('');
    try {
      setReceipt(await issueNodeRegistration(
        String(form.get('nodeId')).trim() || undefined, Number(form.get('ttl')), form.has('colocated'),
      ));
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (busy || !revokeId) return;
    setBusy(true);
    setError('');
    try {
      await revokeNode(revokeId);
      setRevokeId('');
      if (receipt?.nodeId === revokeId) setReceipt(undefined);
      refresh();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <details className="resource-panel node-registration"><summary>管理节点注册</summary>
    <div className="resource-panel-body">
      <form className="workbench-fields" onSubmit={issue}>
        <label>节点 ID<input name="nodeId" pattern="[a-z0-9][a-z0-9\-]{0,62}" placeholder="留空自动分配" /></label>
        <label>有效期（秒）<input name="ttl" type="number" min={1} max={900} defaultValue={600} required /></label>
        <label><input name="colocated" type="checkbox" /> 与网关共享物理 GPU</label>
        <button type="submit" disabled={busy}>签发注册码</button>
      </form>
      {receipt && <div className="node-registration-receipt" role="status">
        <p><strong>{receipt.nodeId}</strong> · 注册码有效至 {localTime(receipt.expiresAt)}</p>
        <label>一次性注册码<input readOnly value={receipt.code} autoComplete="off" /></label>
        <p>在节点 Runner 上使用 <code>--node-id {receipt.nodeId} --registration-code</code> 及此注册码。</p>
        <button type="button" onClick={() => setReceipt(undefined)}>隐藏注册码</button>
      </div>}
      {nodes.some(node => node.id !== 'local') && <div className="workbench-fields">
        <label>要吊销的节点<select value={revokeId} disabled={busy} onChange={event => setRevokeId(event.target.value)}>
          <option value="">选择节点</option>
          {nodes.filter(node => node.id !== 'local').map(node => <option key={node.id}>{node.id}</option>)}
        </select></label>
        <button type="button" disabled={busy || !revokeId} onClick={() => void revoke()}>吊销节点令牌</button>
      </div>}
      {error && <p className="resource-error" role="alert">{error}</p>}
    </div>
  </details>;
}
