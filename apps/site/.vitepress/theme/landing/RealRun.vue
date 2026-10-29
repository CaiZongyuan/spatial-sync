<script setup lang="ts">
// One real run on the Synthetic laboratory (lab-demo@3), captured on 2026-10-21:
// the transcript is the actual lw session, the pictures are the actual Web pages.
import { withBase } from 'vitepress'
import StatusChip from './StatusChip.vue'

type Line = { kind: 'cmd' | 'ok' | 'bad' | 'note'; text: string }
const RUN: Line[] = [
  { kind: 'note', text: '# 提交操作计划：离心两支样本并开始培养' },
  { kind: 'cmd', text: 'lw plan create plan.yaml' },
  { kind: 'note', text: '# 计划里有高风险动作（设定培养箱温度），整份计划进入待审批' },
  { kind: 'cmd', text: 'lw do /World/Lab/RobotA pickPlace from=rack.A1 to=centrifuge.slot1 --plan $PLAN --step 1' },
  { kind: 'bad', text: 'inv-3913e6d8… rejected pickPlace plan_not_approved: This plan revision requires approval' },
  { kind: 'note', text: '# 在 Web 的待审批页批准之后' },
  { kind: 'cmd', text: 'lw do /World/Lab/RobotA pickPlace from=rack.A1 to=centrifuge.slot1 --plan $PLAN --step 1' },
  { kind: 'ok', text: 'inv-a3964e8e… succeeded pickPlace' },
  { kind: 'cmd', text: 'lw do /World/Lab/RobotA pickPlace from=rack.A2 to=centrifuge.slot3 --plan $PLAN --step 2' },
  { kind: 'ok', text: 'inv-0d66b960… succeeded pickPlace' },
  { kind: 'cmd', text: 'lw do /World/Lab/Centrifuge closeLid --plan $PLAN --step 3' },
  { kind: 'bad', text: 'inv-51197df8… rejected closeLid precondition_failed: Action conditions are not satisfied\nMove the arm out of the centrifuge zone first' },
  { kind: 'cmd', text: 'lw do /World/Lab/Incubator setSetpoint temperatureC=37 --plan $PLAN --step 5' },
  { kind: 'ok', text: 'inv-1fbeaa1a… succeeded setSetpoint' },
  { kind: 'note', text: '# 机械臂还在离心机区域，互锁拦下了关盖（随后的启动也因盖子开着被拒）。插入第 7 步「回到原位」，提交修订 2，再次获批' },
  { kind: 'cmd', text: 'lw plan revise $PLAN plan.yaml' },
  { kind: 'cmd', text: 'lw do /World/Lab/RobotA home --plan $PLAN --step 7' },
  { kind: 'ok', text: 'inv-5ecfbff1… succeeded home' },
  { kind: 'cmd', text: 'lw do /World/Lab/Centrifuge closeLid --plan $PLAN --step 3' },
  { kind: 'ok', text: 'inv-36eba3f9… succeeded closeLid' },
  { kind: 'cmd', text: 'lw do /World/Lab/Centrifuge start rpm=3000 durationSeconds=30 --plan $PLAN --step 4 --no-wait' },
  { kind: 'ok', text: 'inv-91c378a3… accepted start' },
]
</script>

<template>
  <section id="real-run" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>08 · What Exists Today</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">愿景很远，</span><span class="nb">但基础已经开始运行</span></h2>
      <p class="pl-section-lede">我们已经建立了一个可以运行的实验室信息与空间工作台：场景与资产有版本，Newton 与 Synthetic 仿真会话在浏览器里实时可见，Web、命令行和 MCP 通过同一条执行管线操作设备。设备操作声明参数、前置条件和互锁，高风险动作需要人批准，每一次调用、结果和设备事件都被记录。</p>
      <StatusChip status="delivered" />
    </div>

    <h3 class="run-head"><span class="nb">一次真实的运行，</span><span class="nb">包括被拦下的那两步</span></h3>
    <p class="run-lede">这是 Synthetic 仿真实验室里的一次真实运行，下面是命令行节选。计划里有高风险动作，整份计划先被拦下等待批准；获批后机械臂完成搬运，但关盖时它还没离开离心机区域，再次被互锁拒绝；计划修订、重新获批，机械臂先回到安全位置，离心机才继续执行。</p>

    <div class="grid">
      <figure class="term">
        <div class="term-bar"><span class="dots"><i /><i /><i /></span><span class="mono">lab-demo@3 · Synthetic · 节选</span></div>
        <pre><code><span v-for="(l, i) in RUN" :key="i" class="line" :class="l.kind"><b v-if="l.kind === 'cmd'">$ </b>{{ l.text }}
</span></code></pre>
      </figure>
      <figure class="pl-figure shot tall">
        <img :src="withBase('/images/product/workbench.webp')" alt="会话工作台：实时空间视图下方是关联调用列表，显示 start 运行中、closeLid 和 home 已成功" width="1173" height="1225" loading="lazy" decoding="async" />
        <figcaption><span class="mono">会话工作台 · 实际截图</span>实时空间视图，以及这次运行的每一个调用。</figcaption>
      </figure>
    </div>
    <div class="wide">
      <figure class="pl-figure shot">
        <img :src="withBase('/images/product/approvals.webp')" alt="待审批页：计划「离心两支样本并开始培养」等待批准，右侧活动时间线记录了提交、批准与再次提交" width="1600" height="507" loading="lazy" decoding="async" />
        <figcaption><span class="mono">人工关口 · 实际截图</span>提交、批准、修订后再提交，每一步都留在时间线里。</figcaption>
      </figure>
    </div>

    <div class="proof">
      <p class="proof-claim"><span class="nb">在 Lab World 中，</span><span class="nb">动作不是一条发出后</span><span class="nb">就被假定成功的命令。</span></p>
      <p class="proof-body">它有前置条件、执行状态、结果检查、拒绝原因、操作记录和必要的人类控制。这次运行不是真实硬件上的自主实验，也不是完整的机器人学习闭环；它证明的是这一切赖以成立的工程基础。场景自动构建、复杂机器人任务训练、真实硬件集成与 Real2Sim 驱动的策略改进，正沿着这套基础设施逐步推进。</p>
    </div>
  </section>
</template>

<style scoped>
.pl-section-head :deep(.pl-chip) { margin-top: 18px; }
.pl .run-head { margin-top: 72px; font-size: clamp(22px, 2.4vw, 28px); line-height: 1.35; }
.pl .run-lede { margin-top: 14px; max-width: 760px; font-size: 15.5px; line-height: 1.85; color: var(--ink-2); }
.grid { margin-top: 36px; display: grid; grid-template-columns: 1.2fr 1fr; gap: 24px; align-items: start; }
.wide { margin-top: 24px; }
.term { margin: 0; border-radius: 14px; border: 1px solid var(--line); background: var(--term-bg); box-shadow: var(--shadow-1); overflow: hidden; }
.term-bar { display: flex; align-items: center; gap: 14px; height: 38px; padding: 0 14px; border-bottom: 1px solid var(--term-line); color: var(--term-dim); font-size: 11px; }
.dots { display: flex; gap: 6px; }
.dots i { width: 9px; height: 9px; border-radius: 50%; background: var(--term-dot); }
pre { margin: 0; padding: 18px 20px 22px; overflow-x: auto; font-family: var(--font-mono); font-size: 12.5px; line-height: 1.85; color: var(--term-ink); }
.line { white-space: pre-wrap; overflow-wrap: anywhere; }
.line b { font-weight: 400; color: var(--term-dim); }
.line.note { color: var(--term-dim); }
.line.ok { color: var(--term-ok); }
.line.bad { color: var(--term-bad); }
.shot img { background: var(--figure-ground); }
.proof { margin-top: 72px; display: grid; grid-template-columns: 1fr 1fr; gap: 56px; align-items: start; padding-top: 32px; border-top: 1px solid var(--line-2); }
.pl .proof-claim { font-family: var(--font-display); font-weight: 600; font-size: clamp(24px, 2.8vw, 34px); line-height: 1.45; color: var(--ink); }
.pl .proof-body { font-size: 15.5px; line-height: 1.9; color: var(--ink-2); }
.tall img { aspect-ratio: auto; }

@media (max-width: 900px) {
  .grid, .proof { grid-template-columns: 1fr; }
  .proof { gap: 20px; }
  pre { font-size: 11.5px; }
}
</style>
