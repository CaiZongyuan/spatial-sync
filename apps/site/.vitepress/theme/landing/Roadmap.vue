<script setup lang="ts">
// From one workcell to a platform: three validations, the commercial path, and scale.
import { withBase } from 'vitepress'

const PROOFS = [
  { n: '第一次验证', title: '一个完整任务，能可靠运行', body: '从场景构建、仿真检查到设备执行，完成一个真正可验证的 Workcell 工作流。正常情况能跑通；异常发生时，能留下证据、安全停止或恢复。' },
  { n: '第二次验证', title: '真实反馈，能改善下一次运行', body: '真实设备接入数字世界，实际运动与传感器结果反向校准仿真，形成可重现的失败测试。Agent 提出改进，经训练或程序修订产生候选技能，独立评估与批准后重新部署。' },
  { n: '第三次验证', title: '同样的方法，适用于更多工作站', body: '不同实验室有不同设备，但共享操作模式、任务结构和评估方法。适配、任务定义与技能验证逐步标准化，Lab World 从单个实验室的工具，成为多个物理工作站共同使用的平台。' },
]

const BUSINESS = [
  { phase: '起步', items: ['工作站部署', '设备集成', '企业方案'], note: '用真实交付创造价值' },
  { phase: '成熟', items: ['软件订阅', '企业级部署', '训练与评估', '远程运行服务'], note: '重复性能力沉淀为可复用的产品' },
  { phase: '更远', items: ['工业 Workcell', '柔性制造', '机器人技能开发'], note: '一切需要可靠物理执行的场景' },
]
</script>

<template>
  <section id="roadmap" class="rm">
    <div class="pl-section">
      <p class="pl-eyebrow pl-rule"><span>12 · From Workcell to Platform</span></p>
      <div class="pl-section-head">
        <h2><span class="nb">从一座实验室，</span><span class="nb">走向更广泛的 Physical AI</span></h2>
        <p class="pl-section-lede">产品路线沿着三次验证推进。我们不假设所有实验室都能立刻变成完全自主的系统，而是从已经验证的工作流程开始，逐步提高可复用性与自动化程度。</p>
      </div>

      <ol class="proofs">
        <li v-for="(p, i) in PROOFS" :key="p.n">
          <span class="dot" :class="{ first: i === 0 }" />
          <p class="n mono">{{ p.n }}</p>
          <h3>{{ p.title }}</h3>
          <p class="body">{{ p.body }}</p>
        </li>
      </ol>

      <div class="biz">
        <p class="biz-k mono">商业化路径</p>
        <div class="biz-row">
          <div v-for="b in BUSINESS" :key="b.phase" class="biz-col">
            <p class="phase">{{ b.phase }}</p>
            <p class="items"><span v-for="it in b.items" :key="it">{{ it }}</span></p>
            <p class="note">{{ b.note }}</p>
          </div>
        </div>
      </div>
    </div>

    <figure class="rm-figure">
      <img :src="withBase('/images/landing/scale.webp')" alt="俯瞰成百上千座微缩实验室组成的网格，靛蓝色的光在实验室之间流动，一直延伸到远处" width="1600" height="800" loading="lazy" decoding="async" />
    </figure>
    <p class="rm-line"><span class="nb">实验室是我们的起点，</span><span class="nb">但物理智能的基础能力</span><span class="nb">不应只属于实验室。</span></p>
  </section>
</template>

<style scoped>
.proofs { list-style: none; margin: 64px 0 0; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 32px; position: relative; }
.proofs::before { content: ''; position: absolute; left: 0; right: 0; top: 7px; height: 1px; background: linear-gradient(90deg, var(--accent), var(--line-2) 40%, var(--line-2)); }
.proofs li { position: relative; padding-top: 32px; }
.dot { position: absolute; left: 0; top: 0; width: 15px; height: 15px; border-radius: 50%; border: 1.5px solid var(--ghost); background: var(--ground); }
.dot.first { border-color: var(--accent); background: var(--accent); }
.pl .n { font-size: 11px; letter-spacing: .08em; color: var(--faint); }
.pl .proofs h3 { margin-top: 10px; font-size: 22px; line-height: 1.35; }
.pl .proofs .body { margin-top: 10px; font-size: 14.5px; line-height: 1.85; color: var(--ink-2); }

.biz { margin-top: 80px; }
.pl .biz-k { font-size: 11px; letter-spacing: .08em; color: var(--faint); }
.biz-row { margin-top: 16px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.biz-col { padding: 20px 22px; border-radius: 14px; border: 1px solid var(--line); background: var(--surface); box-shadow: var(--shadow-1); }
.pl .phase { font-family: var(--font-display); font-weight: 600; font-size: 20px; color: var(--ink); }
.pl .items { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px; }
.items span { padding: 3px 9px; border-radius: 6px; border: 1px solid var(--line-2); font-size: 13px; color: var(--ink-2); }
.pl .biz-col .note { margin-top: 12px; font-size: 13px; color: var(--muted); }

.rm-figure { margin: 88px 0 0; }
.rm-figure img { display: block; width: 100%; height: min(64vh, 660px); object-fit: cover; background: var(--figure-ground);
  -webkit-mask-image: linear-gradient(180deg, transparent, #000 14%, #000 80%, transparent);
  mask-image: linear-gradient(180deg, transparent, #000 14%, #000 80%, transparent); }
.pl .rm-line { margin: 36px auto 0; max-width: 1160px; padding: 0 24px; text-align: center; font-family: var(--font-display); font-weight: 600; font-size: clamp(26px, 3.6vw, 48px); line-height: 1.35; color: var(--ink); text-wrap: balance; }

@media (max-width: 1000px) {
  .proofs { grid-template-columns: 1fr; gap: 36px; }
  .proofs::before { left: 7px; right: auto; top: 0; bottom: 0; width: 1px; height: auto; background: linear-gradient(180deg, var(--accent), var(--line-2) 40%); }
  .proofs li { padding: 0 0 0 36px; }
  .biz-row { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .rm-figure img { height: 52vh; }
  .pl .rm-line { padding: 0 16px; text-align: left; }
}
</style>
