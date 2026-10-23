import { Link } from '@tanstack/react-router';

export function ResourceTabs({ current, modelCount, nodeCount }: { current: 'gpu' | 'models'; modelCount?: number; nodeCount?: number }) {
  return <nav className="resource-page-tabs" aria-label="资源标签" onKeyDown={event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const links = [...event.currentTarget.querySelectorAll<HTMLAnchorElement>('a')];
    const currentIndex = links.indexOf(document.activeElement as HTMLAnchorElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? links.length - 1
      : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + links.length) % links.length;
    event.preventDefault(); links[next]?.focus();
  }}>
    <Link to="/resources/gpu" aria-current={current === 'gpu' ? 'page' : undefined}>算力{nodeCount != null && <small>{nodeCount} 节点</small>}</Link>
    <Link to="/resources/models" aria-current={current === 'models' ? 'page' : undefined}>模型
      {modelCount !== undefined && <small>{modelCount} LLM · 机器人模型未接入</small>}
    </Link>
  </nav>;
}
