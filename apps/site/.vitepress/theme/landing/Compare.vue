<script setup lang="ts">
// Labs today against labs on Lab World, each row with its real status.
import { withBase } from 'vitepress'
import StatusChip from './StatusChip.vue'
import type { Status } from './story'

const ROWS: { topic: string; today: string; world: string; status: Status }[] = [
  { topic: '设备', today: '每台设备一套软件和协议，agent 读不懂。', world: '统一的设备类型与动作：能做什么、此刻怎样、缺什么，一查便知。', status: 'delivered' },
  { topic: '流程', today: '人写脚本，人盯着跑。', world: 'agent 把目标拆成操作计划；机械臂的路径，动手之前先预演。', status: 'delivered' },
  { topic: '安全', today: '靠操作员的经验和记性。', world: '前置条件与互锁写进设备定义，危险动作会被拦下，高风险动作要人批准。', status: 'delivered' },
  { topic: '失败', today: '靠人发现，靠人记录。', world: '每一步都有录制和历史读数，agent 回看录制，定位是哪一步出了问题。', status: 'delivered' },
  { topic: '改进', today: '请专家重新调参、重新示教。', world: 'agent 起草训练计划，在并行仿真里训练；评估过线、人批准后接回流程。', status: 'planned' },
  { topic: '规模', today: '每座实验室从头再来。', world: '一处学到的策略和技能进入共享能力库，所有实验室一起变好。', status: 'vision' },
]
</script>

<template>
  <section id="compare" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>实验室的瓶颈</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">瓶颈不在想法，</span><span class="nb">在动手</span></h2>
      <p class="pl-section-lede">验证一个科学假设，往往要做几十上百次实验。今天，这些实验靠人一步步做、一项项记。机器人和仪器都在，缺的是让 agent 看得懂、动得了、学得会的那一层。</p>
    </div>

    <div class="pair">
      <figure class="pl-figure">
        <img :src="withBase('/images/landing/lab-today.webp')" alt="一张实验台：仪器各自独立，线缆缠绕，机械臂停在半空，旁边是空白的记录板" width="1559" height="1009" loading="lazy" decoding="async" />
        <figcaption><span class="mono">今天</span>设备各自为政，流程和失败都记在人的脑子里。</figcaption>
      </figure>
      <figure class="pl-figure">
        <img :src="withBase('/images/landing/lab-world.webp')" alt="同一张实验台：仪器整齐排列，由靛蓝色光线连到上方的核心，机械臂正把试管放进离心机" width="1600" height="900" loading="lazy" decoding="async" />
        <figcaption><span class="mono">Lab World</span>设备、流程、失败和改进，都在同一个闭环里。</figcaption>
      </figure>
    </div>

    <div class="table" role="table" aria-label="今天的实验室与 Lab World 的对比">
      <div class="row head" role="row">
        <span role="columnheader" />
        <span role="columnheader">今天</span>
        <span role="columnheader">有了 Lab World</span>
      </div>
      <div v-for="r in ROWS" :key="r.topic" class="row" role="row">
        <span class="topic" role="rowheader">{{ r.topic }}</span>
        <span class="today" role="cell">{{ r.today }}</span>
        <span class="world" role="cell">{{ r.world }}<StatusChip :status="r.status" /></span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.pair { margin-top: 56px; display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.pair img { aspect-ratio: 16 / 10; object-fit: cover; }
.table { margin-top: 56px; border-top: 1px solid var(--line-2); }
.row { display: grid; grid-template-columns: 120px 1fr 1.25fr; gap: 24px; padding: 18px 0; border-bottom: 1px solid var(--line); align-items: baseline; }
.row.head { padding: 12px 0; font-family: var(--font-mono); font-size: 11px; letter-spacing: .06em; color: var(--faint); }
.topic { font-family: var(--font-display); font-weight: 600; font-size: 18px; color: var(--ink); }
.today { font-size: 14.5px; line-height: 1.75; color: var(--muted); }
.world { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; font-size: 14.5px; line-height: 1.75; color: var(--ink); }

@media (max-width: 900px) {
  .pair { grid-template-columns: 1fr; }
  .row { grid-template-columns: 1fr; gap: 6px; }
  .row.head { display: none; }
  .today::before { content: '今天：'; color: var(--faint); }
}
</style>
