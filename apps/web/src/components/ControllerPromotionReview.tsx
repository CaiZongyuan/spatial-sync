import { useEffect, useState } from 'react';
import { request } from '../lib/api';

type Score = { evaluationId: string; subject: string; successes: number; episodes: number; score: number };
interface Review {
  status: string; candidateBelowReference?: boolean; error?: { code: string };
  train?: Record<'reference' | 'candidate', Score>;
  test?: Record<'reference' | 'candidate', Score>;
  qualification?: { evidenceMode?: string };
}
const percent = (score: number) => `${(score * 100).toFixed(1)}%`;

export function ControllerPromotionReview({ id, canRead, ready, reportStatus, refreshKey }: {
  id: string; canRead: boolean; ready: (value: boolean) => void; reportStatus: (value: string) => void; refreshKey: string;
}) {
  const [review, setReview] = useState<Review>(), [error, setError] = useState<string>(), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let disposed = false;
    ready(false); setReview(undefined); setError(undefined);
    if (!canRead) return;
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 8000);
    void request<Review>(`/controller-promotions/${encodeURIComponent(id)}/review`, { signal: controller.signal })
      .then(value => { if (!disposed) { setReview(value); ready(Boolean(value.test) && value.status === 'awaiting_human'); reportStatus(value.status); } })
      .catch(failure => { if (!disposed) setError(controller.signal.aborted ? '读取超时，请重试' : (failure as Error).message); })
      .finally(() => clearTimeout(timeout));
    return () => { disposed = true; controller.abort(); clearTimeout(timeout); };
  }, [id, canRead, ready, reportStatus, refreshKey, attempt]);
  return <section className="agent-promotion-review" aria-label="Controller 晋升私有评估">
    <h4>人类部署评估</h4><p>训练接受已更新统计 Reference；本次批准发布新的 Controller 和默认场景。已有会话保留原版本。</p>
    {!canRead ? <p>操作员可查看本次私有评估。</p> : error ? <p role="alert">晋升评估暂不可用：{error} <button onClick={() => setAttempt(value => value + 1)}>重试评估</button></p> :
      !review ? <p role="status">正在读取晋升评估…</p> : !review.test ? <p role="status">评估尚未完整：{review.status}</p> : <>
        {review.qualification?.evidenceMode === 'controlled' && <p>受控工程证据：不代表真实训练或接触质量。</p>}
        {review.candidateBelowReference && <p className="resource-error below-reference" role="alert">候选的 held-out 成功率低于 Reference。请评估回退风险后再决定是否部署。</p>}
        <table><thead><tr><th>固定版本</th><th>Train 成功率</th><th>Held-out 成功率</th><th>有效测试次数</th></tr></thead><tbody>
          {(['reference', 'candidate'] as const).map(side => <tr key={side}><th>{side === 'reference' ? '前一 Reference' : '当前接受候选'}<small>{review.test![side].subject}</small></th>
            <td>{review.train ? percent(review.train[side].score) : '未知'}</td><td>{percent(review.test![side].score)}</td><td>{review.test![side].successes} / {review.test![side].episodes}</td></tr>)}
        </tbody></table>
      </>}
    {review?.error && <p role="alert">部署受阻：{review.error.code}</p>}
  </section>;
}
