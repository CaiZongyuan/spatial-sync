<script setup lang="ts">
// What Lab World is made of: three parts, and the loop they turn together
// between the digital and the physical world.
import { PARTS } from './story'

const DETAIL: Record<string, { stages: string; body: string }> = {
  world: { stages: 'Build · Simulate', body: '把真实实验室变成可计算、可仿真的世界：外观、几何、物理行为和设备语义，一层层建起来，并且可以检查、版本化和复用。' },
  brain: { stages: 'Act · Observe', body: '理解目标，规划步骤，调用工具与设备，核对每一个结果；被拒绝就修订计划，拿不准就请人介入。' },
  rsi: { stages: 'Learn → Build', body: '从每一次真实反馈中训练新策略、写下新技能，评估过线、人批准后接回流程。下一轮，站在这一轮的结果上。' },
}

const LOOP = [
  { en: 'Build', zh: '构建世界', part: 'world' },
  { en: 'Simulate', zh: '仿真预演', part: 'world' },
  { en: 'Act', zh: '真实执行', part: 'brain' },
  { en: 'Observe', zh: '观察反馈', part: 'brain' },
  { en: 'Learn', zh: '自我改进', part: 'rsi' },
]
</script>

<template>
  <section id="platform" class="pl-section" tabindex="-1">
    <p class="pl-eyebrow pl-rule"><span>03 · Introducing Lab World</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">三个核心构成，</span><span class="nb">一个持续运转的闭环</span></h2>
      <p class="pl-section-lede">Lab World 是面向物理环境的 <span lang="en">Agentic Execution &amp; Learning Platform</span>。它把虚拟场景、物理仿真、智能模型、设备控制和真实世界反馈连在一起，让原本分散的能力围绕同一个任务协同工作，并且越用越好。</p>
    </div>

    <ol class="eq" aria-label="Lab World 等于 World Building 加 Agentic 大脑加 RSI 进化">
      <li class="brand"><span lang="en">Lab World</span></li>
      <li v-for="(p, i) in PARTS" :key="p.id" class="part" :class="p.id">
        <span class="op" aria-hidden="true">{{ i === 0 ? '=' : '+' }}</span>
        <a :href="p.href">
          <span class="n mono">{{ String(i + 1).padStart(2, '0') }} · {{ DETAIL[p.id].stages }}</span>
          <b>{{ p.name }}</b>
          <span class="line">{{ p.line }}</span>
          <span class="body">{{ DETAIL[p.id].body }}</span>
          <span class="more mono">详细 ↓</span>
        </a>
      </li>
    </ol>

    <div class="loop" role="img" aria-label="Build 与 Simulate 属于 World Building，Act 与 Observe 属于 Agentic 大脑，Learn 属于 RSI 进化，再回到 Build">
      <div class="groups" aria-hidden="true">
        <span v-for="p in PARTS" :key="p.id" :class="p.id">{{ p.name }}</span>
      </div>
      <ol class="nodes">
        <li v-for="(n, i) in LOOP" :key="n.en" :class="n.part">
          <span class="dot" />
          <span class="en" lang="en">{{ n.en }}</span>
          <span class="zh">{{ n.zh }}</span>
          <span v-if="i === 1" class="bridge mono" lang="en">Sim2Real →</span>
        </li>
      </ol>
      <div class="back">
        <svg viewBox="0 0 1000 40" preserveAspectRatio="none" aria-hidden="true"><path d="M900 2 V22 Q900 36 886 36 H114 Q100 36 100 22 V8" /><path class="head" d="M94 12 L100 3 L106 12" /></svg>
        <span class="mono"><span lang="en">Real2Sim</span> · 现实反馈，修正数字世界</span>
      </div>
    </div>
    <p class="foot">三者缺一不可：没有 <b>World Building</b>，Agent 无处预演；没有 <b>Agentic 大脑</b>，世界只是一个模型；没有 <b>RSI 进化</b>，每一次失败都白白浪费。</p>
  </section>
</template>

<style scoped>
.eq { list-style: none; margin: 56px 0 0; padding: 0; display: grid; grid-template-columns: auto 1fr 1fr 1fr; gap: 12px; align-items: stretch; }
.brand { display: flex; align-items: center; padding-right: 26px; font-family: var(--font-display); font-weight: 600; font-size: clamp(28px, 3vw, 38px); line-height: 1.1; color: var(--ink); white-space: nowrap; }
.part { position: relative; display: flex; }
.op { position: absolute; left: -11px; top: 50%; translate: -50% -50%; z-index: 1; display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; background: var(--ground); border: 1px solid var(--line-2); font-family: var(--font-mono); font-size: 12px; color: var(--muted); }
.part a { flex: 1; display: flex; flex-direction: column; padding: 22px 22px 18px; border-radius: 16px; border: 1px solid var(--line); background: var(--surface); box-shadow: var(--shadow-1); transition: border-color .2s var(--ease), transform .3s var(--ease); }
.part a:hover { border-color: var(--accent); transform: translateY(-2px); }
.part .n { font-size: 10.5px; letter-spacing: .06em; color: var(--faint); }
.part b { margin-top: 12px; font-family: var(--font-display); font-weight: 600; font-size: 26px; line-height: 1.2; color: var(--ink); }
.part .line { margin-top: 6px; font-size: 14px; color: var(--accent-ink); }
.part .body { margin-top: 12px; font-size: 14px; line-height: 1.8; color: var(--ink-2); }
.part .more { margin-top: auto; padding-top: 14px; font-size: 11px; color: var(--faint); }

.loop { margin-top: 56px; padding: 26px 32px 22px; border: 1px solid var(--line); border-radius: 18px; background: color-mix(in srgb, var(--surface) 72%, transparent); box-shadow: var(--shadow-1); }
.groups { display: grid; grid-template-columns: 2fr 2fr 1fr; gap: 12px; }
.groups span { padding-bottom: 10px; border-bottom: 1px solid var(--line-2); font-family: var(--font-mono); font-size: 11px; letter-spacing: .06em; color: var(--muted); text-align: center; }
.groups .rsi { color: var(--accent-ink); border-bottom-color: var(--accent); }
.nodes { list-style: none; margin: 26px 0 0; padding: 0; display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
.nodes li { position: relative; display: flex; flex-direction: column; align-items: center; text-align: center; }
.nodes li + li::before { content: ''; position: absolute; top: 7px; right: calc(50% + 14px); width: calc(100% - 16px); height: 1px; background: var(--line-2); }
.dot { width: 15px; height: 15px; border-radius: 50%; border: 1.5px solid var(--ghost); background: var(--surface); }
.brain .dot { border-color: var(--ink-2); }
.rsi .dot { border-color: var(--accent); background: var(--accent); }
.en { margin-top: 14px; font-family: var(--font-display); font-weight: 600; font-size: 24px; color: var(--ink); }
.zh { margin-top: 4px; font-size: 13px; color: var(--muted); }
.bridge { position: absolute; top: -18px; left: 100%; translate: -50% 0; font-size: 10.5px; color: var(--accent-ink); white-space: nowrap; }
.back { margin-top: 10px; position: relative; }
.back svg { display: block; width: 100%; height: 40px; overflow: visible; }
.back path { fill: none; stroke: var(--accent); stroke-width: 1.4; vector-effect: non-scaling-stroke; }
.back .head { stroke-linecap: round; stroke-linejoin: round; }
.back > span { position: absolute; left: 50%; bottom: -2px; translate: -50% 0; padding: 0 12px; background: var(--surface); font-size: 11px; color: var(--accent-ink); white-space: nowrap; }
.pl .foot { margin-top: 32px; max-width: 900px; font-size: 17px; line-height: 1.85; color: var(--ink-2); }
.foot b { font-weight: 600; color: var(--ink); }

@media (max-width: 1100px) {
  .eq { grid-template-columns: 1fr; gap: 14px; }
  .brand { padding: 0; }
  .op { left: 50%; top: -7px; }
}
@media (max-width: 720px) {
  .loop { padding: 22px 18px; }
  .groups, .back { display: none; }
  .nodes { grid-template-columns: 1fr; gap: 18px; margin-top: 0; }
  .nodes li { flex-direction: row; align-items: baseline; gap: 12px; text-align: left; }
  .nodes li + li::before { display: none; }
  .dot { flex: none; align-self: center; }
  .en { margin-top: 0; font-size: 20px; }
  .zh { margin-top: 0; }
  .bridge { display: none; }
  .nodes li:last-child::after { content: '↺ Real2Sim'; margin-left: auto; font-family: var(--font-mono); font-size: 11px; color: var(--accent-ink); }
  .pl .foot { font-size: 15.5px; }
}
</style>
