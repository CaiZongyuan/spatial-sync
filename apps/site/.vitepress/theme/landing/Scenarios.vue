<script setup lang="ts">
// Where the same loop applies first: three kinds of experiment, each from one Goal.
import { withBase } from 'vitepress'
import StatusChip from './StatusChip.vue'
import type { Status } from './story'

const CASES: { field: string; goal: string; body: string; image: string; alt: string; status: Status }[] = [
  {
    field: '生物 · 细胞培养', goal: '在 4 个培养温度下比较样本生长，找出最佳条件。',
    body: 'agent 排出培养与检测的时间表，连续数天轮转培养箱、离心机和检测仪；哪一步变差了，它自己回看录制找原因。',
    image: 'scenario-biology', alt: '微缩细胞培养实验室：打开的培养箱里放着培养瓶，生物安全柜旁的机械臂握着移液器', status: 'vision',
  },
  {
    field: '化学与材料 · 配方筛选', goal: '在 12 种配比里，找出最稳定的那一种。',
    body: '移液、称量和记录交给机器人。agent 根据每一轮的结果决定下一轮做哪几个配比，而不是把所有组合都试一遍。',
    image: 'scenario-materials', alt: '微缩化学实验室：一排玻璃小瓶，龙门式移液机器人正在加液，旁边是手套箱和天平', status: 'vision',
  },
  {
    field: '机器人 · 技能训练', goal: '把插管这一步的成功率，提到 95% 以上。',
    body: '从失败录制里定位问题，在并行仿真里训练新策略；评估过线、人批准之后，接回真实流程。',
    image: 'scenario-robotics', alt: '一台机械臂在实验台上把试管插进离心机，旁边一排半透明的同款机械臂在并行练习同一个动作', status: 'planned',
  },
]
</script>

<template>
  <section id="scenarios" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>能用在哪里</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">一句目标，</span><span class="nb">换一种实验</span></h2>
      <p class="pl-section-lede">Lab World 不绑定某一种实验。只要设备能被描述、结果能被测量，agent 就能接手。下面是我们最先想做到的三类场景。</p>
    </div>
    <div class="cases">
      <article v-for="c in CASES" :key="c.field">
        <figure class="pl-figure">
          <img :src="withBase(`/images/landing/${c.image}.webp`)" :alt="c.alt" width="1600" height="900" loading="lazy" decoding="async" />
        </figure>
        <p class="field">{{ c.field }}<StatusChip :status="c.status" /></p>
        <p class="goal">「{{ c.goal }}」</p>
        <p class="body">{{ c.body }}</p>
      </article>
    </div>
  </section>
</template>

<style scoped>
.cases { margin-top: 56px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 32px; }
.cases img { aspect-ratio: 4 / 3; object-fit: cover; }
.pl .field { margin-top: 20px; display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--muted); }
.pl .goal { margin-top: 12px; font-family: var(--font-display); font-weight: 600; font-size: 21px; line-height: 1.5; color: var(--ink); }
.pl .body { margin-top: 10px; font-size: 14.5px; line-height: 1.8; color: var(--ink-2); }
@media (max-width: 1000px) { .cases { grid-template-columns: 1fr; gap: 48px; } .cases img { aspect-ratio: 16 / 9; } }
</style>
