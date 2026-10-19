import { createFileRoute, Link, useNavigate } from '@tanstack/react-router';
import { useState } from 'react';
import { PlatformOverview } from '../components/PlatformOverview';

export const Route = createFileRoute('/sessions/')({ component: Sessions });

function Sessions() {
  const navigate = useNavigate();
  const [sessionId, setSessionId] = useState('');
  return <main className="home">
    <PlatformOverview page="sessions" />
    <section className="scene-session-entry" id="new-session" aria-label="启动或加入会话">
      <div><h2>从场景开始</h2><p>在场景库检查版本、验证结果与物理源，然后启动会话。</p><Link to="/scenes" className="button-link primary">打开场景库 →</Link></div>
      <form onSubmit={event => {
        event.preventDefault();
        if (sessionId.trim()) void navigate({ to: '/sessions/$sessionId', params: { sessionId: sessionId.trim() } });
      }}><h2>加入现有会话</h2><p>每个观察者共享同一权威空间状态，镜头独立。</p><label>会话 ID<input aria-label="Session ID" placeholder="粘贴会话 ID" value={sessionId} onChange={event => setSessionId(event.target.value)} /></label><button disabled={!sessionId.trim()} type="submit">Connect to session →</button></form>
    </section>
  </main>;
}
