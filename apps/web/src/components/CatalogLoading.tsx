import './catalog-loading.css';

export function CatalogLoadError({ page, error, onRetry, onRefresh }: {
  page: number; error: string; onRetry: () => void; onRefresh: () => void;
}) {
  return <div className="asset-empty" role="alert"><h2>第 {page} 页加载失败</h2>
    <p>请重试当前页，或重新查询最新目录。</p>
    <div><button onClick={onRetry}>重试</button> <button onClick={onRefresh}>重新查询最新目录</button></div>
    <details><summary>错误信息</summary><p>{error}</p></details>
  </div>;
}

export function CatalogLoading({ page, count, view }: { page: number; count: number; view?: 'card' | 'list' }) {
  return <section className="catalog-loading" aria-label="素材结果" aria-busy="true">
    <p className="catalog-loading-status" role="status" aria-label="素材加载状态">
      <span className="catalog-loading-dot" aria-hidden="true" />正在加载第 {page} 页 · 每页 {count} 项
    </p>
    <div className={`catalog-result-grid${view === 'list' ? ' is-list' : ''}`} aria-hidden="true">
      {Array.from({ length: count }, (_, index) => <div className="catalog-card catalog-skeleton-card" key={index}>
        <div className="catalog-card-figure catalog-skeleton-surface" />
        <div className="catalog-skeleton-copy">
          <span className="catalog-skeleton-line is-short catalog-skeleton-surface" />
          <span className="catalog-skeleton-line is-title catalog-skeleton-surface" />
          <span className="catalog-skeleton-line catalog-skeleton-surface" />
          <span className="catalog-skeleton-line is-short catalog-skeleton-surface" />
        </div>
      </div>)}
    </div>
  </section>;
}
