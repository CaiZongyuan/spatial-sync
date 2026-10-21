<script setup lang="ts">
// Why recursive self-improvement is buildable now, and why it has to reach the lab.
import { withBase } from 'vitepress'

const NOW = [
  { title: 'agent 能长时间工作', body: '大模型 agent 已经能连续数小时调用工具、读写代码，并根据结果修改自己的指令和技能。' },
  { title: '仿真足够快', body: 'GPU 物理仿真可以同时跑成百上千个环境，机器人可以先在仿真里练，再到真实设备上做。' },
  { title: '评估可以自动化', body: '每一次改动都能在同一套评估集上打分，好不好不再靠感觉，也不再靠人逐条复核。' },
]
</script>

<template>
  <section id="why-now" class="pl-section" tabindex="-1">
    <p class="pl-eyebrow pl-rule"><span>为什么是现在</span></p>
    <div class="grid">
      <div class="essay">
        <h2><span class="nb">智能爆炸，</span><span class="nb">从思想实验</span><span class="nb">变成了工程问题</span></h2>
        <p>1965 年，数学家 I. J. Good 写下了「智能爆炸」：设计机器本身是一种智力活动，所以一台足够聪明的机器，能设计出比自己更好的机器；后者再设计出更好的，如此递归下去。</p>
        <p>此后半个多世纪，这只是一个思想实验。现在不一样了：大模型 agent 能读写代码、调用工具，并根据结果修改自己。递归自我改进（RSI）第一次可以落到一个具体的循环里：改动自己的一部分，用反馈判断好坏，只保留更好的版本。</p>
        <p>但这个循环有一个常被忽略的前提：反馈必须来自外部。只和自己对话的系统，学不到它原本不知道的东西。软件里，外部反馈是测试和基准；科学里，外部反馈只有一个来源：实验。</p>
        <p class="claim">所以 RSI 要走出屏幕，就必须进入实验室。<span class="nb">Lab World</span> 就是为这件事搭的平台。</p>
      </div>
      <figure class="pl-figure">
        <img :src="withBase('/images/landing/why-now.webp')" alt="一座座实验室模块盘旋上升，越往上越精细、越明亮" width="1600" height="900" loading="lazy" decoding="async" />
        <figcaption><span class="mono">图 · 递归</span>每一轮改进，都站在上一轮的结果上。</figcaption>
      </figure>
    </div>

    <blockquote class="quote">
      <p><span class="nb">「第一台超智能机器，</span><span class="nb">将是人类需要做出的</span><span class="nb">最后一项发明。」</span></p>
      <footer><span lang="en">“The first ultraintelligent machine is the last invention that man need ever make.”</span><cite>I. J. Good，<i lang="en">Speculations Concerning the First Ultraintelligent Machine</i>，1965</cite></footer>
    </blockquote>

    <div class="now">
      <p class="now-head">三件事，同时成立了</p>
      <ol>
        <li v-for="(n, i) in NOW" :key="n.title">
          <span class="n mono">{{ String(i + 1).padStart(2, '0') }}</span>
          <h3>{{ n.title }}</h3>
          <p>{{ n.body }}</p>
        </li>
      </ol>
    </div>
  </section>
</template>

<style scoped>
.grid { margin-top: 32px; display: grid; grid-template-columns: 1fr 1.05fr; gap: 64px; align-items: start; }
.pl .essay h2 { font-size: clamp(32px, 3.8vw, 50px); line-height: 1.18; }
.pl .essay p { margin-top: 20px; font-size: 16.5px; line-height: 1.9; color: var(--ink-2); }
.pl .essay h2 + p { margin-top: 28px; }
.pl .essay .claim { margin-top: 28px; padding-left: 18px; border-left: 2px solid var(--accent); font-family: var(--font-display); font-weight: 600; font-size: 21px; line-height: 1.6; color: var(--ink); }
.pl-figure { margin-top: 8px; }

.quote { margin: 96px auto 0; padding: 0; border: 0; max-width: 920px; text-align: center; }
.pl .quote p { font-family: var(--font-display); font-weight: 600; font-size: clamp(26px, 3.4vw, 42px); line-height: 1.45; color: var(--ink); text-wrap: balance; }
.quote footer { margin-top: 20px; display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--muted); }
.quote footer span { font-family: var(--font-display); font-style: italic; color: var(--ink-2); }
.quote cite { font-style: normal; font-size: 12px; color: var(--faint); }

.now { margin-top: 96px; }
.pl .now-head { font-family: var(--font-mono); font-size: 11.5px; letter-spacing: .08em; color: var(--faint); }
.now ol { list-style: none; margin: 20px 0 0; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 32px; }
.now li { padding-top: 20px; border-top: 1px solid var(--line-2); }
.now .n { font-size: 12px; color: var(--faint); }
.pl .now h3 { margin-top: 12px; font-size: 22px; line-height: 1.35; }
.pl .now li p { margin-top: 10px; font-size: 14.5px; line-height: 1.8; color: var(--ink-2); }

@media (max-width: 1000px) {
  .grid { grid-template-columns: 1fr; gap: 40px; }
  .now ol { grid-template-columns: 1fr; gap: 24px; }
}
@media (max-width: 620px) {
  .pl .essay p { font-size: 15.5px; }
  .pl .essay .claim { font-size: 18px; }
  .quote { margin-top: 64px; text-align: left; }
  .now { margin-top: 64px; }
}
</style>
