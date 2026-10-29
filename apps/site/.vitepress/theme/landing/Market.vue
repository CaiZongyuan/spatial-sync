<script setup lang="ts">
// The first market: laboratory workcells, why labs, who buys first, and the expansion path.
import { withBase } from 'vitepress'

const WHY = [
  { title: '明确的任务目标', body: '样本要转运，试剂要分配，设备要按规定运行，结果要被记录。' },
  { title: '长期的集成痛点', body: '大量专业设备，长期面对多厂商集成、流程编排和数据割裂。' },
  { title: '可验证的成功条件', body: '实验操作有明确的成功条件、过程约束与记录要求，天然适合评估 Agent 与物理策略。' },
  { title: '高价值的技术需求', body: '实验场景同时连接科研、工业和高价值的技术需求。' },
]

const PATH = [
  { step: '起点', title: '试管转运与离心协同', body: '一台机械臂、少量设备、一类确定任务构成的 Workcell。', image: '/images/blog/physical-ai-workbench.webp', alt: '实验台上，机械臂夹着一支试管伸向打开的离心机，旁边是试管架和一台相机', w: 1536, h: 1024 },
  { step: '扩展', title: '移液与样本处理', body: '更多设备、更长的流程，复用已经验证的适配、任务与技能。', image: '/images/landing/scenario-materials.webp', alt: '微缩实验台：一排玻璃小瓶，龙门式移液机器人正在加液，旁边是手套箱和天平', w: 1600, h: 900 },
  { step: '深入', title: '细胞培养流程', body: '跨越数天的长程流程：培养、取样、检测与异常恢复。', image: '/images/landing/scenario-biology.webp', alt: '微缩细胞培养实验室：打开的培养箱里放着培养瓶，生物安全柜旁的机械臂握着移液器', w: 1600, h: 900 },
]

const VALUE = ['降低流程开发与集成的重复劳动', '改善执行的可追溯性', '减少异常排查的成本', '提升设备的有效利用']
</script>

<template>
  <section id="market" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>09 · Our First Market</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">从实验室 Workcell 切入，</span><span class="nb">而不是从所有机器人开始</span></h2>
      <p class="pl-section-lede">Physical AI 可以用在制造、物流、医疗、科研和更多行业。但一个真正能落地的平台，需要从可定义、可验证、可交付的工作流程开始。我们选择实验室自动化。</p>
    </div>

    <ol class="why">
      <li v-for="(w, i) in WHY" :key="w.title">
        <span class="n mono">{{ String(i + 1).padStart(2, '0') }}</span>
        <h3>{{ w.title }}</h3>
        <p>{{ w.body }}</p>
      </li>
    </ol>

    <div class="who">
      <p class="who-k mono">首批客户</p>
      <p class="who-v">需要建设或升级自动化工作站的<span class="nb"><b>科研机构</b>、</span><span class="nb"><b>生物技术企业</b></span>和<span class="nb"><b>实验室集成方</b>。</span></p>
    </div>

    <ol class="path">
      <li v-for="p in PATH" :key="p.title">
        <figure class="pl-figure"><img :src="withBase(p.image)" :alt="p.alt" :width="p.w" :height="p.h" loading="lazy" decoding="async" /></figure>
        <p class="step mono">{{ p.step }}</p>
        <h3>{{ p.title }}</h3>
        <p class="body">{{ p.body }}</p>
      </li>
    </ol>

    <div class="value">
      <div>
        <p class="value-k mono">我们要验证的，不只是机器人动作</p>
        <ul><li v-for="v in VALUE" :key="v">{{ v }}</li></ul>
        <p class="value-note">这些价值将用实际部署前后的数据来衡量，而不是用概念性的效率数字代替。</p>
      </div>
      <p class="claim"><span class="nb">先让一个 Workcell 真正有用，</span><span class="nb">再让第二个 Workcell 更容易交付。</span></p>
    </div>
  </section>
</template>

<style scoped>
.why { list-style: none; margin: 56px 0 0; padding: 0; display: grid; grid-template-columns: repeat(4, 1fr); gap: 0 32px; }
.why li { padding-top: 20px; border-top: 1px solid var(--line-2); }
.why .n { font-size: 11.5px; color: var(--faint); }
.pl .why h3 { margin-top: 12px; font-size: 20px; line-height: 1.35; }
.pl .why p { margin-top: 10px; font-size: 14.5px; line-height: 1.8; color: var(--ink-2); }

.who { margin-top: 56px; display: flex; align-items: baseline; gap: 24px; padding: 22px 26px; border-radius: 14px; border: 1px solid var(--line); background: var(--surface); box-shadow: var(--shadow-1); }
.pl .who-k { flex: none; font-size: 11px; letter-spacing: .08em; color: var(--accent-ink); }
.pl .who-v { font-size: 17px; line-height: 1.7; color: var(--ink-2); }
.who-v b { font-weight: 600; color: var(--ink); }

.path { list-style: none; margin: 72px 0 0; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 32px; }
.path img { aspect-ratio: 4 / 3; object-fit: cover; }
.pl .step { margin-top: 20px; font-size: 11px; letter-spacing: .08em; color: var(--faint); }
.path li:first-child .step { color: var(--accent-ink); }
.pl .path h3 { margin-top: 8px; font-size: 22px; }
.pl .path .body { margin-top: 8px; font-size: 14.5px; line-height: 1.8; color: var(--ink-2); }

.value { margin-top: 80px; display: grid; grid-template-columns: 1fr 1.1fr; gap: 56px; align-items: center; padding-top: 32px; border-top: 1px solid var(--line-2); }
.pl .value-k { font-size: 11px; letter-spacing: .08em; color: var(--faint); }
.value ul { list-style: none; margin: 16px 0 0; padding: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 10px 20px; }
.value li { position: relative; padding-left: 16px; font-size: 15px; line-height: 1.6; color: var(--ink); }
.value li::before { content: ''; position: absolute; left: 0; top: .62em; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
.pl .value-note { margin-top: 16px; font-size: 13.5px; line-height: 1.7; color: var(--muted); }
.pl .claim { font-family: var(--font-display); font-weight: 600; font-size: clamp(24px, 2.8vw, 34px); line-height: 1.45; color: var(--ink); text-wrap: balance; }

@media (max-width: 1000px) {
  .why { grid-template-columns: 1fr 1fr; gap: 32px; }
  .path { grid-template-columns: 1fr; gap: 48px; }
  .path img { aspect-ratio: 16 / 9; }
  .value { grid-template-columns: 1fr; gap: 32px; }
}
@media (max-width: 620px) {
  .why { grid-template-columns: 1fr; gap: 24px; }
  .who { flex-direction: column; gap: 8px; padding: 18px 20px; }
  .pl .who-v { font-size: 15.5px; }
  .value ul { grid-template-columns: 1fr; }
}
</style>
