<script setup lang="ts">
// What the platform is built from, each part with its real delivery status.
import StatusChip from './StatusChip.vue'
import type { Status } from './story'

const PARTS: { title: string; status: Status; body: string; meta: string }[] = [
  { title: '一个事实源', status: 'delivered', body: '物理源（Newton 或 Synthetic）是唯一的事实。每台设备是什么类型、能做什么、此刻读数如何，人和 agent 读到的是同一份。', meta: 'Newton · Synthetic' },
  { title: '一条动作管线', status: 'delivered', body: 'Web、lw CLI 和 MCP 走同一条管线：校验参数、检查前置条件与互锁、执行、记录。agent 没有后门。', meta: 'HTTP API · lw · MCP' },
  { title: '训练与评估', status: 'planned', body: '训练作业、训练视图和平台内评估。agent 用它练出新策略，再用同一套评估集衡量好坏。', meta: 'T1–T3' },
  { title: '人工关口', status: 'delivered', body: '高风险动作和删除库版本必须由人批准。批准训练计划、提升新策略，也将走同一套关口。', meta: '每次批准都有记录' },
]
</script>

<template>
  <section class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>平台由什么组成</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">四个部分，</span><span class="nb">撑起整条闭环</span></h2>
      <p class="pl-section-lede">动画里的每一步，背后都是这四个部分。</p>
    </div>
    <ol class="parts">
      <li v-for="(p, i) in PARTS" :key="p.title">
        <span class="n">{{ String(i + 1).padStart(2, '0') }}</span>
        <h3>{{ p.title }}</h3>
        <StatusChip :status="p.status" />
        <p class="body">{{ p.body }}</p>
        <p class="meta mono">{{ p.meta }}</p>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.parts { list-style: none; margin: 56px 0 0; padding: 0; display: grid; grid-template-columns: repeat(4, 1fr); gap: 0 32px; }
li { display: flex; flex-direction: column; align-items: flex-start; padding-top: 24px; border-top: 1px solid var(--line-2); }
.n { font-family: var(--font-display); font-size: 40px; line-height: 1; color: var(--ghost); }
.pl h3 { margin-top: 20px; font-size: 24px; line-height: 1.3; }
li :deep(.pl-chip) { margin-top: 12px; }
.pl .body { margin-top: 14px; font-size: 14.5px; line-height: 1.8; color: var(--ink-2); }
.pl .meta { margin-top: 14px; font-size: 11px; letter-spacing: .04em; color: var(--faint); }

@media (max-width: 1000px) {
  .parts { grid-template-columns: repeat(2, 1fr); gap: 40px 32px; }
}
@media (max-width: 620px) {
  .parts { grid-template-columns: 1fr; gap: 32px; margin-top: 40px; }
}
</style>
