<script setup lang="ts">
// Lab World coordinates the open Physical AI ecosystem instead of rebuilding it.
const GUARANTEES = ['场景有版本', '设备有能力接口', '操作有前置条件', '训练有可复现的任务', '策略有可信的评估', '真实运行有持续反馈']

const STACK = [
  { role: '数字资产', names: ['Blender', 'CAD', '生成式 3D'] },
  { role: '场景描述', names: ['OpenUSD'] },
  { role: '物理仿真', names: ['Newton', 'MuJoCo', 'PhysX'] },
  { role: '训练与评估', names: ['Isaac Lab'] },
  { role: '物理技能', names: ['VLA', 'World Action Model'] },
]

const NEXT = [
  { when: '更强的生成模型出现', then: '用它们创建更好的场景。' },
  { when: '更先进的物理引擎出现', then: '用它们做更有效的验证。' },
  { when: '新的机器人策略出现', then: '在相同任务条件下比较，它们是否真正改善了结果。' },
]
</script>

<template>
  <section id="ecosystem" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>10 · Built on the Ecosystem</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">我们不需要</span><span class="nb">重新发明所有技术</span></h2>
      <p class="pl-section-lede">Physical AI 正在形成一个快速发展的开放技术生态。Lab World 不重复实现这些能力，而是围绕它们建立一个面向真实物理任务的协调与执行平台。</p>
    </div>

    <div class="stack" role="img" aria-label="技术栈：Lab World 协调层位于开放生态之上，开放生态之下是真实的机器人、仪器与传感器">
      <div class="band top">
        <p class="band-k"><b>Lab World</b><span>协调与执行层</span></p>
        <ul><li v-for="g in GUARANTEES" :key="g">{{ g }}</li></ul>
      </div>
      <div class="eco">
        <div v-for="s in STACK" :key="s.role" class="col">
          <p class="role">{{ s.role }}</p>
          <p class="names"><span v-for="n in s.names" :key="n" lang="en">{{ n }}</span></p>
        </div>
      </div>
      <div class="band bottom"><p>机器人 · 仪器 · 传感器 · 真实的物理世界</p></div>
    </div>

    <ul class="next">
      <li v-for="n in NEXT" :key="n.when"><span class="when">当{{ n.when }}，</span><span class="then">Lab World {{ n.then }}</span></li>
    </ul>

    <p class="claim"><span class="nb">底层模型可以持续变化；</span><span class="nb">围绕真实任务积累的流程、约束、</span><span class="nb">执行证据和技能资产，会持续保留。</span></p>
  </section>
</template>

<style scoped>
.stack { margin-top: 56px; display: flex; flex-direction: column; gap: 10px; }
.band { border-radius: 16px; border: 1px solid var(--line); }
.band.top { padding: 24px 26px; border-color: color-mix(in srgb, var(--accent) 50%, var(--line)); background: color-mix(in srgb, var(--accent) 8%, var(--surface)); box-shadow: var(--shadow-1); }
.pl .band-k { display: flex; align-items: baseline; gap: 14px; }
.band-k b { font-family: var(--font-display); font-weight: 600; font-size: 24px; color: var(--ink); }
.band-k span { font-size: 13px; color: var(--accent-ink); }
.band.top ul { list-style: none; margin: 16px 0 0; padding: 0; display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; }
.band.top li { padding: 10px 12px; border-radius: 9px; background: var(--surface); border: 1px solid var(--line); font-size: 13px; line-height: 1.5; color: var(--ink); }
.eco { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
.col { padding: 18px 18px 20px; border-radius: 14px; border: 1px solid var(--line); background: var(--surface); }
.pl .role { font-family: var(--font-mono); font-size: 11px; letter-spacing: .06em; color: var(--faint); }
.pl .names { margin-top: 10px; display: flex; flex-direction: column; gap: 4px; }
.names span { font-family: var(--font-display); font-weight: 600; font-size: 17px; line-height: 1.4; color: var(--ink); }
.band.bottom { padding: 14px 20px; background: var(--surface-2); text-align: center; }
.pl .band.bottom p { font-family: var(--font-mono); font-size: 11.5px; letter-spacing: .06em; color: var(--muted); }

.next { list-style: none; margin: 64px 0 0; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 32px; }
.next li { padding-top: 18px; border-top: 1px solid var(--line-2); display: flex; flex-direction: column; gap: 6px; font-size: 15px; line-height: 1.75; }
.when { color: var(--muted); }
.then { color: var(--ink); }
.pl .claim { margin-top: 72px; max-width: 920px; font-family: var(--font-display); font-weight: 600; font-size: clamp(24px, 2.8vw, 34px); line-height: 1.5; color: var(--ink); text-wrap: balance; }

@media (max-width: 1000px) {
  .band.top ul { grid-template-columns: repeat(3, 1fr); }
  .eco { grid-template-columns: repeat(3, 1fr); }
  .next { grid-template-columns: 1fr; gap: 20px; }
}
@media (max-width: 620px) {
  .band.top { padding: 20px; }
  .band.top ul { grid-template-columns: 1fr 1fr; }
  .eco { grid-template-columns: 1fr 1fr; }
  .pl .claim { margin-top: 56px; }
}
</style>
