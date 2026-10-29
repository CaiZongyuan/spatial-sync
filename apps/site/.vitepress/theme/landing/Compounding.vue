<script setup lang="ts">
// The compounding advantage: each deployment leaves verified, reusable capability behind.
const R = 140
const C = 200
// Clockwise from the top left; each label sits on the ring, each arrow between two labels.
const NODES = ['更多任务', '更多真实反馈', '更好的测试与技能', '更低的部署成本'].map((label, i) => {
  const a = (-135 + i * 90) * Math.PI / 180
  return { label, left: `${((C + R * Math.cos(a)) / (2 * C)) * 100}%`, top: `${((C + R * Math.sin(a)) / (2 * C)) * 100}%` }
})
const ARROWS = [0, 90, 180, 270].map((deg) => {
  const a = (deg - 90) * Math.PI / 180
  return { x: C + R * Math.cos(a), y: C + R * Math.sin(a), deg }
})

const LIBRARIES = ['版本化的资产库', '设备能力库', '任务库', '评估基准', '经过验证的技能库']
</script>

<template>
  <section id="compounding" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>11 · The Compounding Advantage</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">一次部署建立能力，</span><span class="nb">下一次部署复用经验</span></h2>
      <p class="pl-section-lede">在一个 Workcell 里，RSI 让下一次运行更好；在更多部署之间，它让下一次交付更容易。Lab World 的长期价值，不只来自软件功能的增加，更来自真实使用中持续积累的工程资产。第一座实验室带来设备适配、场景模型、操作流程、任务测试和运行证据；第二座实验室也许用的是不同品牌的机器人，但其中一部分操作语义、任务定义、验证程序和异常处理经验可以复用。</p>
    </div>

    <div class="grid">
      <div class="wheel" role="img" aria-label="飞轮：更多任务带来更多真实反馈，更多真实反馈带来更好的测试与技能，更好的测试与技能降低部署成本，更低的部署成本带来更多任务">
        <svg :viewBox="`0 0 ${C * 2} ${C * 2}`" aria-hidden="true">
          <circle class="ring" :cx="C" :cy="C" :r="R" />
          <circle class="ring-in" :cx="C" :cy="C" :r="R - 34" />
          <path v-for="a in ARROWS" :key="a.deg" class="arrow" d="M-5 -6 L3 0 L-5 6" :transform="`translate(${a.x} ${a.y}) rotate(${a.deg})`" />
        </svg>
        <span v-for="n in NODES" :key="n.label" class="node" :style="{ left: n.left, top: n.top }">{{ n.label }}</span>
        <span class="core"><b>Lab World</b><span>持续积累的能力</span></span>
      </div>

      <div class="side">
        <p class="k mono">随着更多 Workcell 接入，逐步积累</p>
        <ul class="libs"><li v-for="l in LIBRARIES" :key="l">{{ l }}</li></ul>
        <div class="note">
          <h3>不只是更多数据</h3>
          <p>原始轨迹不会自动变成可迁移的技能，一台机器人上的成功也不能未经验证就部署到另一台。真正有价值的是：我们不仅保留成功的结果，还知道它是在什么设备、什么环境、什么限制和什么验证条件下取得的。</p>
        </div>
        <div class="note">
          <h3>有边界的共享</h3>
          <p>跨客户的能力共享以数据许可、知识产权、安全和隐私边界为前提。平台复用通用适配器、验证方法和经过许可的技能，不默认汇集各实验室的原始实验数据。</p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.grid { margin-top: 56px; display: grid; grid-template-columns: 1fr 1fr; gap: 64px; align-items: center; }
.wheel { position: relative; width: 100%; max-width: 460px; aspect-ratio: 1; margin: 0 auto; }
.wheel svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.ring { fill: none; stroke: var(--accent); stroke-width: 1.5; opacity: .7; }
.ring-in { fill: color-mix(in srgb, var(--accent) 5%, transparent); stroke: var(--line); stroke-dasharray: 3 5; }
.arrow { fill: none; stroke: var(--accent); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.node { position: absolute; translate: -50% -50%; padding: 8px 14px; border-radius: 999px; border: 1px solid var(--line-2); background: var(--surface); box-shadow: var(--shadow-1); font-size: 14px; color: var(--ink); white-space: nowrap; }
.core { position: absolute; left: 50%; top: 50%; translate: -50% -50%; display: flex; flex-direction: column; align-items: center; gap: 4px; }
.core b { font-family: var(--font-display); font-weight: 600; font-size: 26px; color: var(--ink); }
.core span { font-size: 12px; color: var(--muted); }
.pl .k { font-size: 11px; letter-spacing: .08em; color: var(--faint); }
.libs { list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
.libs li { padding: 6px 12px; border-radius: 8px; border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--line-2)); font-size: 13.5px; color: var(--accent-ink); }
.note { margin-top: 28px; padding-top: 18px; border-top: 1px solid var(--line-2); }
.pl .note h3 { font-size: 20px; }
.pl .note p { margin-top: 8px; font-size: 14.5px; line-height: 1.85; color: var(--ink-2); }

@media (max-width: 1000px) {
  .grid { grid-template-columns: 1fr; gap: 48px; }
}
@media (max-width: 480px) {
  .node { font-size: 12.5px; padding: 6px 10px; }
  .core b { font-size: 21px; }
}
</style>
