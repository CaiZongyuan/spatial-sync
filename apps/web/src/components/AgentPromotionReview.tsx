import { useEffect, useState } from 'react';
import { request } from '../lib/api';
import { agentMean, type AgentPromotionReview as Review } from '../lib/agent-rsi';

export function AgentPromotionReview({ id, canRead, ready, reportStatus, refreshKey }: {
  id: string; canRead: boolean; ready: (value: boolean) => void; reportStatus: (value: string) => void; refreshKey: string;
}) {
  const [review, setReview] = useState<Review>(), [error, setError] = useState<string>(), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let disposed = false;
    ready(false); setReview(undefined); setError(undefined);
    if (!canRead) return;
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 8000);
    void request<Review>(`/agent-promotions/${encodeURIComponent(id)}/review`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) { setReview(value); ready(Boolean(value.test)); reportStatus(value.status); } })
      .catch(failure => { if (!disposed) setError(controller.signal.aborted ? '读取超时，请重试' : (failure as Error).message); })
      .finally(() => clearTimeout(timeout));
    return () => { disposed = true; controller.abort(); clearTimeout(timeout); };
  }, [id, canRead, attempt, ready, reportStatus, refreshKey]);
  return <section className="agent-promotion-review" aria-label="Agent 晋升私有评估">
    <h4>人类晋升评估</h4><p>批准后才会推进 Reference；训练接受与晋升决定分别保留。</p>
    {review?.errorCode && <p role="alert">晋升恢复受阻：{review.errorCode}。处理保留的文件冲突后可重试恢复。</p>}
    {!canRead ? <p>操作员可查看本次晋升评估。</p> : error ? <p role="alert">晋升评估暂不可用：{error} <button onClick={() => setAttempt(value => value + 1)}>重试评估</button></p> :
      !review ? <p role="status">正在读取晋升评估…</p> : !review.test ? <p role="status">评估尚未完整：{review.status}</p> : <>
        {review.candidateBelowReference && <p className="resource-error below-reference" role="alert">候选的 held-out 得分低于 Reference。请评估回退风险后再决定是否晋升。</p>}
        <table><thead><tr><th>固定版本</th><th>Held-out 平均分</th><th>有效题次</th></tr></thead><tbody>
          {(['reference', 'candidate'] as const).map(side => <tr key={side}><th>{side === 'reference' ? 'Reference' : '候选'} v{review.test![side].harnessVersion}</th><td>{agentMean(review.test![side].result?.score)}</td><td>{review.test![side].cases.filter(value => value.status === 'completed').length}</td></tr>)}
        </tbody></table><details><summary>逐题私有证据</summary>{(['reference', 'candidate'] as const).map(side => <div key={side}><strong>{side}</strong><ul>{review.test![side].cases.map(item => <li key={item.caseRunId}>{item.caseId} · 第 {item.repeat} 次 · {agentMean(item.score)} · {item.status}</li>)}</ul></div>)}</details>
      </>}
  </section>;
}
