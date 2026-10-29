<script setup lang="ts">
// The long-term vision and the closing call to action; it ends the page.
import { withBase } from 'vitepress'
import { PARTS, type Part } from './story'

const emit = defineEmits<{ replay: [] }>()

const LADDER = ['一台机器人', '一个 Workcell', '一座自动化实验室', '连接不同设备、技能与物理环境的平台']

// The page's closing recap: the three parts, one promise each.
const PROMISE: Record<Part, string> = {
  world: '让 Agent 构建和理解物理世界，在动手之前先预演。',
  brain: '让 Agent 理解目标、操控真实设备，并核对每一个结果。',
  rsi: '让每一次真实反馈，都推动下一轮可检验的改进。',
}
</script>

<template>
  <section id="vision" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>The Long-Term Vision</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">当每个物理系统，</span><span class="nb">都能成为 Agent 的工作环境</span></h2>
      <p class="pl-section-lede">今天，AI Agent 正在改变人类使用软件的方式。下一步，是让 Agent 以同样自然的方式理解和使用物理系统：从现实中构建数字世界，在仿真中预测行动，在真实设备上执行任务，再从真实结果中改进自己的世界模型和操作能力。</p>
    </div>

    <ol class="ladder">
      <li v-for="(l, i) in LADDER" :key="l"><span class="n mono">{{ String(i + 1).padStart(2, '0') }}</span>{{ l }}</li>
    </ol>
    <p class="future">未来，科学研究不必让每个新实验都从头编写自动化流程；机器人开发不必让每次失败都只留在一份调试记录里；物理设备也不必永远只执行孤立、固定的指令。真实世界中的每一次行动，都可能成为下一次行动的依据。通向这个未来的，不是一次模型升级，而是一系列真实、可验证的工程进步。</p>

    <p class="recap"><span lang="en">Lab World</span> · 迈向物理 AI</p>
    <ol class="mantras">
      <li v-for="p in PARTS" :key="p.id">
        <p class="en">{{ p.name }}</p>
        <p class="zh">{{ PROMISE[p.id] }}</p>
      </li>
    </ol>

    <div class="finale">
      <p class="big" lang="en">The physical world should not be a black box to AI Agents.</p>
      <p class="sub"><span class="nb">我们在构建一个让 Agent 能够理解物理世界、</span><span class="nb">可靠执行任务，</span><span class="nb">并从真实反馈中递归自我改进的平台。</span></p>
      <p class="sub strong"><span class="nb">从一座实验室开始，</span><span class="nb">走向一个可以被 Agent 理解、操作和持续改进的物理世界。</span></p>
      <div class="actions">
        <a class="pl-btn pl-btn--primary" href="#platform" lang="en">Explore the Platform</a>
        <button class="pl-btn" type="button" lang="en" @click="emit('replay')"><svg viewBox="0 0 16 16"><path d="M5 3.2v9.6L12.6 8z" fill="currentColor" /></svg>View the Live Demo</button>
      </div>
    </div>

    <footer class="foot">
      <span><span class="mono">Lab World</span> · 迈向物理 AI</span>
      <nav><a :href="withBase('/blog/')">博客</a><a :href="withBase('/guide/getting-started')">文档</a></nav>
    </footer>
  </section>
</template>

<style scoped>
.ladder { list-style: none; margin: 56px 0 0; padding: 0; display: grid; grid-template-columns: 1fr 1fr 1fr 1.5fr; gap: 10px; }
.ladder li { position: relative; display: flex; flex-direction: column; gap: 10px; padding: 18px 20px; border-radius: 14px; border: 1px solid var(--line); background: var(--surface); font-family: var(--font-display); font-weight: 600; font-size: 18px; line-height: 1.45; color: var(--ink); }
.ladder li:last-child { border-color: color-mix(in srgb, var(--accent) 50%, var(--line)); background: color-mix(in srgb, var(--accent) 8%, var(--surface)); }
.ladder .n { font-family: var(--font-mono); font-weight: 400; font-size: 11px; color: var(--faint); }
.pl .future { margin-top: 40px; max-width: 820px; font-size: 16.5px; line-height: 1.9; color: var(--ink-2); }

.pl .recap { margin-top: 96px; font-family: var(--font-mono); font-size: 11.5px; letter-spacing: .08em; color: var(--accent-ink); }
.mantras { list-style: none; margin: 18px 0 0; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 32px; }
.mantras li { padding-top: 20px; border-top: 1px solid var(--line-2); }
.pl .mantras .en { font-family: var(--font-display); font-weight: 600; font-size: clamp(22px, 2.2vw, 28px); line-height: 1.25; color: var(--ink); }
.pl .mantras .zh { margin-top: 10px; font-size: 14.5px; line-height: 1.75; color: var(--ink-2); }

.finale { margin-top: 136px; text-align: center; display: flex; flex-direction: column; align-items: center; }
.pl .big { max-width: 980px; font-family: var(--font-display); font-weight: 600; font-style: italic; font-size: clamp(32px, 4.6vw, 60px); line-height: 1.2; color: var(--ink); text-wrap: balance; }
.pl .sub { margin-top: 24px; max-width: 760px; font-size: 17px; line-height: 1.8; color: var(--ink-2); }
.pl .sub.strong { margin-top: 8px; color: var(--ink); }
.actions { margin-top: 36px; display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }
.actions button { font: inherit; font-size: 14px; font-weight: 500; }

.foot { display: flex; justify-content: space-between; gap: 16px; margin-top: 136px; padding: 20px 0 40px; border-top: 1px solid var(--line); font-size: 12px; color: var(--faint); }
.foot nav { display: flex; gap: 20px; }
.foot a { color: var(--muted); }
.foot a:hover { color: var(--ink); }

@media (max-width: 1000px) {
  .ladder { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 620px) {
  .ladder, .mantras { grid-template-columns: 1fr; }
  .pl .recap { margin-top: 64px; }
  .mantras { gap: 24px; }
  .finale { margin-top: 96px; text-align: left; align-items: flex-start; }
  .pl .sub { font-size: 15.5px; }
  .actions { justify-content: flex-start; }
  .foot { flex-direction: column; gap: 6px; margin-top: 96px; }
}
</style>
