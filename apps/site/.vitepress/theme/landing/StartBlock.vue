<script setup lang="ts">
import { withBase } from 'vitepress'
const repo = 'https://github.com/CaiZongyuan/spatial-sync'
</script>

<template>
  <section class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>开始</span></p>
    <div class="sb-inner">
      <div class="pl-section-head">
        <h2><span class="nb">在你的电脑上，</span><span class="nb">打开第一座实验室</span></h2>
        <p class="pl-section-lede">Synthetic 模式不需要 GPU。几条命令之后，你和你的 agent 就能通过同一个平台操作仿真设备。</p>
        <div class="actions">
          <a class="pl-btn pl-btn--primary" :href="withBase('/guide/getting-started')">快速开始</a>
          <a class="pl-btn" :href="repo" target="_blank" rel="noreferrer"><svg viewBox="0 0 16 16"><path fill="currentColor" d="M8 .2a8 8 0 0 0-2.5 15.6c.4 0 .5-.2.5-.4v-1.5c-2.2.5-2.7-1-2.7-1-.4-.9-.9-1.2-.9-1.2-.7-.5.1-.5.1-.5.8.1 1.2.8 1.2.8.7 1.3 1.9.9 2.3.7.1-.5.3-.9.5-1.1-1.8-.2-3.6-.9-3.6-4 0-.9.3-1.6.8-2.1-.1-.2-.4-1 .1-2.1 0 0 .7-.2 2.2.8a7.6 7.6 0 0 1 4 0c1.5-1 2.2-.8 2.2-.8.4 1.1.2 1.9.1 2.1.5.6.8 1.3.8 2.1 0 3.1-1.9 3.8-3.6 4 .3.3.6.8.6 1.5v2.2c0 .2.1.5.6.4A8 8 0 0 0 8 .2z" /></svg>GitHub</a>
        </div>
      </div>
      <pre class="code"><code><span class="c"># 安装</span>
uv sync --frozen --group dev
pnpm install --frozen-lockfile

<span class="c"># 启动 API 与 Web</span>
uv run --frozen python -m uvicorn \
  apps.api.app.main:app --port 8000
pnpm dev

<span class="c"># 让 agent 看看实验室</span>
uv run lw ls
uv run lw show /World/Lab/Centrifuge</code></pre>
    </div>
    <footer class="foot">
      <span class="mono">Lab RSI Platform</span>
      <span>早期预览 · 动画为示意</span>
    </footer>
  </section>
</template>

<style scoped>
.sb-inner { display: grid; grid-template-columns: 1fr 1.05fr; gap: 56px; align-items: start; }
.sb-inner .pl-section-head h2 { font-size: clamp(30px, 3.6vw, 44px); }
.actions { margin-top: 28px; display: flex; gap: 10px; flex-wrap: wrap; }
.code { margin: 32px 0 0; padding: 20px 22px; border-radius: 12px; background: var(--ground-2); border: 1px solid var(--line); font-family: var(--font-mono); font-size: 12.5px; line-height: 1.75; color: var(--ink-2); overflow-x: auto; white-space: pre; }
.c { color: var(--faint); }
.foot { display: flex; justify-content: space-between; gap: 16px; margin-top: 120px; padding-bottom: 40px; padding-top: 20px; border-top: 1px solid var(--line); font-size: 12px; color: var(--faint); }
@media (max-width: 900px) {
  .sb-inner { grid-template-columns: 1fr; gap: 32px; }
}
@media (max-width: 560px) {
  .foot { flex-direction: column; gap: 6px; margin-top: 88px; }
}
</style>
