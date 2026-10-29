<script setup lang="ts">
// Real status, kept in step with AGENTS.md and the issue tracker. No invented numbers.
import StatusChip from './StatusChip.vue'
import type { Status } from './story'

const GROUPS: { status: Status; items: string[] }[] = [
  { status: 'delivered', items: ['单一物理源（Newton 或 Synthetic），空间状态实时同步到浏览器', 'Web、命令行与 MCP 共用一条动作管线：前置条件、互锁与人工批准', '场景工厂：Agent 检索素材、调用 Blender 组装场景', '场景与素材库：不可变版本、验证报告与 Newton 预览', '传感器、告警、历史与 Agent 收件箱；真实设备经 Device Host 与 MQTT 接入', '操作计划、逐步核对与机械臂路径预览', '训练、评估、提升与回滚的工程链路；Agent Harness 版本化'] },
  { status: 'progress', items: ['完整贴壁细胞传代仿真：器皿、移液与机械搬运'] },
  { status: 'planned', items: ['学习式接触抓取策略达到正式提升标准', '真实 Workcell 上的端到端任务', 'Real2Sim：用真实运行校准仿真', 'Sim2Sim：跨物理后端验证策略', '真实模型的完整评估集与 Agent 层改进'] },
  { status: 'vision', items: ['由目标驱动的长程自主实验', '跨工作站复用经过验证的技能'] },
]
</script>

<template>
  <section class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>今天的状态</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">从一个个</span><span class="nb">可验证的能力开始</span></h2>
      <p class="pl-section-lede">页首的动画是我们要去的地方。下面是它今天真实的样子：已经交付的能力、正在推进的工作，以及接下来的路线。</p>
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
