<script setup lang="ts">
// What Physical AI is, against the lab software people already know: it acts in
// the physical world, takes the world's feedback, and improves from it, so it
// is Agentic and it is RSI.
const KINDS = [
  {
    id: 'info', name: '信息管理系统', en: 'LIMS · ELN', motto: '记录发生了什么',
    rows: ['不接触设备，靠人录入', '由人阅读、由人判断', '靠人修改流程'],
  },
  {
    id: 'twin', name: '数字孪生', en: 'Digital Twin', motto: '映照正在发生什么',
    rows: ['单向同步，把状态搬到屏幕上', '呈现状态，不负责行动', '模型靠人维护'],
  },
  {
    id: 'pai', name: 'Physical AI', en: 'Lab World', motto: '行动、核对，再变得更好',
    rows: ['双向：能行动，也被现实纠正', '每个动作都有结果检查与证据', '从失败中训练新策略、写下新技能'],
  },
]
const ROW_HEADS = ['和物理世界', '反馈', '改进']
</script>

<template>
  <section id="physical-ai" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>02 · What Is Physical AI</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">Physical AI，</span><span class="nb">不是另一套信息系统，</span><span class="nb">也不只是数字孪生</span></h2>
      <p class="pl-section-lede">实验室里已经有很多软件：信息管理系统记录样本和结果，数字孪生把设备状态搬到屏幕上。它们让人看得更清楚，但不会自己动手，也不会因为一次失败而变得更好。Physical AI 的区别在于闭环：<strong>与物理世界交互，获得真实反馈，再据此改进。</strong></p>
    </div>

    <div class="kinds">
      <article v-for="k in KINDS" :key="k.id" class="kind" :class="k.id">
        <div class="diagram" aria-hidden="true">
          <template v-if="k.id === 'info'">
            <span class="node">实验室</span><span class="link dotted">人</span><span class="node">数据库</span>
          </template>
          <template v-else-if="k.id === 'twin'">
            <span class="node">物理世界</span><span class="link">同步</span><span class="node">三维模型</span>
          </template>
          <template v-else>
            <span class="node">物理世界</span><span class="link both">行动 · 反馈</span><span class="node strong">Agent</span>
            <svg class="loop" viewBox="0 0 200 26" preserveAspectRatio="none"><path d="M178 2 V12 Q178 22 168 22 H32 Q22 22 22 12 V6" /><path class="head" d="M17 10 L22 3 L27 10" /></svg>
            <span class="loop-label mono">改进</span>
          </template>
        </div>
        <p class="en mono" lang="en">{{ k.en }}</p>
        <h3>{{ k.name }}</h3>
        <p class="motto">{{ k.motto }}</p>
        <dl>
          <div v-for="(r, i) in k.rows" :key="r"><dt>{{ ROW_HEADS[i] }}</dt><dd>{{ r }}</dd></div>
        </dl>
      </article>
    </div>

    <div class="defs">
      <div>
        <p class="term" lang="en">Agentic</p>
        <p class="gloss">能自己行动</p>
        <p class="body">理解目标，拆成步骤，调用工具和设备，核对每一步的结果；出了错就修订计划，必要时请人介入。它不执行写死的脚本，而是根据反馈决定下一步。</p>
      </div>
      <div>
        <p class="term" lang="en">RSI</p>
        <p class="gloss">能自己变好 · Recursive Self-Improvement</p>
        <p class="body">改进自己的一部分，用外部反馈判断好坏，只保留更好的版本；下一轮站在这一轮的结果上。只和自己对话的系统，学不到它原本不知道的东西。在物理世界里，外部反馈只能来自真实的设备和实验。</p>
      </div>
    </div>

    <p class="claim"><span class="nb">能交互、能获得反馈、能据此改进：</span><span class="nb">所以 Physical AI 必然是 Agentic 的，</span><span class="nb">也必然走向 RSI。</span></p>
  </section>
</template>

<style scoped>
.pl-section-lede strong { font-weight: 600; color: var(--ink); }
.kinds { margin-top: 56px; display: grid; grid-template-columns: 1fr 1fr 1.15fr; gap: 16px; align-items: stretch; }
.kind { display: flex; flex-direction: column; padding: 24px 24px 22px; border-radius: 16px; border: 1px solid var(--line); background: var(--surface); box-shadow: var(--shadow-1); }
.kind.pai { border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); background: color-mix(in srgb, var(--accent) 7%, var(--surface)); }
.diagram { position: relative; display: flex; align-items: center; gap: 0; height: 64px; padding-bottom: 8px; }
.pai .diagram { padding-bottom: 20px; }
.node { flex: none; padding: 6px 10px; border-radius: 8px; border: 1px solid var(--line-2); background: var(--ground); font-size: 12px; color: var(--ink-2); white-space: nowrap; }
.node.strong { border-color: var(--accent); color: var(--accent-ink); }
.link { position: relative; flex: 1; min-width: 40px; text-align: center; font-size: 10.5px; color: var(--faint); padding-bottom: 14px; }
.link::after { content: ''; position: absolute; left: 6px; right: 6px; bottom: 6px; height: 1px; background: var(--ghost); }
.link::before { content: ''; position: absolute; right: 5px; bottom: 3px; width: 6px; height: 6px; border-top: 1px solid var(--ghost); border-right: 1px solid var(--ghost); transform: rotate(45deg); }
.link.dotted::after { background: none; border-bottom: 1px dashed var(--ghost); }
.link.both { color: var(--accent-ink); }
.link.both::after { background: var(--accent); }
.link.both::before { border-color: var(--accent); }
.loop { position: absolute; left: 0; right: 0; bottom: 0; width: 100%; height: 26px; overflow: visible; }
.loop path { fill: none; stroke: var(--accent); stroke-width: 1.3; vector-effect: non-scaling-stroke; stroke-linecap: round; stroke-linejoin: round; }
.loop-label { position: absolute; left: 50%; bottom: -4px; translate: -50% 0; padding: 0 8px; background: color-mix(in srgb, var(--accent) 7%, var(--surface)); font-size: 10.5px; color: var(--accent-ink); }
.pl .en { margin-top: 18px; font-size: 11px; letter-spacing: .06em; color: var(--faint); }
.pai .en { color: var(--accent-ink); }
.pl .kind h3 { margin-top: 6px; font-size: 26px; }
.pl .motto { margin-top: 6px; font-size: 14.5px; color: var(--muted); }
dl { margin: 18px 0 0; }
dl div { display: grid; grid-template-columns: 72px 1fr; gap: 12px; padding: 10px 0; border-top: 1px solid var(--line); }
dt { font-size: 12px; color: var(--faint); }
dd { margin: 0; font-size: 14px; line-height: 1.6; color: var(--ink-2); }
.pai dd { color: var(--ink); }

.defs { margin-top: 72px; display: grid; grid-template-columns: 1fr 1fr; gap: 48px; }
.defs > div { padding-top: 22px; border-top: 1px solid var(--line-2); }
.pl .term { font-family: var(--font-display); font-weight: 600; font-size: clamp(40px, 5vw, 64px); line-height: 1; color: var(--ink); }
.pl .gloss { margin-top: 12px; font-size: 15px; color: var(--accent-ink); }
.pl .defs .body { margin-top: 12px; font-size: 15.5px; line-height: 1.85; color: var(--ink-2); }
.pl .claim { margin-top: 80px; max-width: 980px; font-family: var(--font-display); font-weight: 600; font-size: clamp(24px, 2.9vw, 36px); line-height: 1.5; color: var(--ink); text-wrap: balance; }

@media (max-width: 1000px) {
  .kinds { grid-template-columns: 1fr; }
  .defs { grid-template-columns: 1fr; gap: 36px; }
}
@media (max-width: 620px) {
  .kind { padding: 20px; }
  .pl .claim { margin-top: 56px; }
}
</style>
