<script setup lang="ts">
// Real status, kept in step with AGENTS.md and the issue tracker. No invented numbers.
import StatusChip from './StatusChip.vue'
import type { Status } from './story'

const GROUPS: { status: Status; items: string[] }[] = [
  { status: 'delivered', items: ['单一物理源（Newton 或 Synthetic）同步到浏览器', 'Web、lw CLI、MCP 共用一条动作管线', '素材库与场景库：不可变版本与验证报告', '传感器、告警、历史与 agent 收件箱（仿真设备）', '操作计划、逐步追踪与机械臂路径预览', '平台 skill：教 agent 用 lw 操作实验室'] },
  { status: 'progress', items: ['计划页与会话页的计划面板', 'fr3-lab 场景导入与抓取验证', '真实设备经 Device Host 与 MQTT 接入'] },
  { status: 'planned', items: ['Claude Code 通过 MCP 走完整条流程', '训练作业、训练视图与平台内评估', 'Agent 与 Controller 两层 RSI、评估中心与成本中心'] },
  { status: 'vision', items: ['由目标驱动的长程自主实验', '跨实验室共享的能力库'] },
]
</script>

<template>
  <section class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>现在能做什么</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">愿景很远，</span><span class="nb">每一步都标清楚</span></h2>
      <p class="pl-section-lede">上面的动画是我们要去的地方。下面是它今天真实的样子。</p>
    </div>
    <div class="sl-cols">
      <div v-for="g in GROUPS" :key="g.status" class="col">
        <StatusChip :status="g.status" />
        <ul><li v-for="i in g.items" :key="i">{{ i }}</li></ul>
      </div>
    </div>
  </section>
</template>

<style scoped>
.sl-cols { margin-top: 56px; display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); }
.col { padding: 20px 20px 8px 0; }
.col + .col { padding-left: 20px; border-left: 1px solid var(--line); }
ul { list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
li { font-size: 13.5px; line-height: 1.55; color: var(--ink-2); }
@media (max-width: 900px) {
  .sl-cols { grid-template-columns: 1fr 1fr; }
  .col:nth-child(3) { padding-left: 0; border-left: 0; }
  .col:nth-child(n + 3) { border-top: 1px solid var(--line); }
}
@media (max-width: 560px) {
  .sl-cols { grid-template-columns: 1fr; }
  .col + .col { padding-left: 0; border-left: 0; border-top: 1px solid var(--line); }
}
</style>
