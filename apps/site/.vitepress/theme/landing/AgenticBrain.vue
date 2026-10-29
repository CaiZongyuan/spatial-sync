<script setup lang="ts">
// Part two, the Agentic brain: it acts on a goal, checks every result, and stays
// inside the safety boundary of the physical system.
import StatusList from './StatusList.vue'
import type { Status } from './story'

const CYCLE = [
  { name: '理解目标', body: '人说要什么，而不是怎么做。' },
  { name: '读懂世界', body: '设备在哪、能做什么、此刻怎样。' },
  { name: '规划', body: '拆成可检查的步骤，写明期望结果。' },
  { name: '预演', body: '路径与动作先在仿真里走一遍。' },
  { name: '执行', body: '调用机器人技能或设备程序。' },
  { name: '核对', body: '读取实际结果，不假设命令已成功。' },
]
const OUTCOMES = [
  { tone: 'ok', name: '通过', body: '进入下一步' },
  { tone: 'warn', name: '被拒绝', body: '找出原因，修订计划' },
  { tone: 'muted', name: '拿不准', body: '停下来，请人介入' },
]

const LAYERS = [
  { en: 'Agent decides what to do.', who: 'Agentic 大脑', items: ['长期目标', '任务分解', '工具选择', '设备协调', '异常恢复'] },
  { en: 'Specialized skills determine how to do it.', who: '专用技能', items: ['控制器', '运动规划器', 'VLA', 'WAM'] },
  { en: 'The physical system verifies what actually happened.', who: '物理系统', items: ['参数限制', '动作互锁', '授权检查', '停止机制'] },
]

const STATUS: { s: Status; t: string }[] = [
  { s: 'delivered', t: 'Web、命令行与 MCP 共用一条动作管线，Agent 没有后门' },
  { s: 'delivered', t: '操作计划、逐步核对、前置条件、互锁与人工批准' },
  { s: 'delivered', t: '真实设备读数经 Device Host 与 MQTT 接入' },
  { s: 'planned', t: 'Sim2Real：机械臂技能从仿真迁移到真机' },
  { s: 'vision', t: '由目标驱动、连续数天的长程自主实验' },
]
</script>

<template>
  <section id="agentic-brain" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>05 · Agentic 大脑</span></p>
    <div class="pl-section-head">
      <p class="part mono">Lab World · 02</p>
      <h2><span class="nb">理解目标、操控设备，</span><span class="nb">并根据反馈行动</span></h2>
      <p class="pl-section-lede">传统自动化要求人事先写好完整流程，一旦出现意外，就得靠额外的异常程序或操作员。Agentic 大脑先理解目标，再读取设备能力和当前状态；它规划任务、调用工具、核对结果，并在环境变化时修改自己的行动方案。</p>
    </div>

    <div class="cycle" aria-label="Agentic 大脑的行动循环">
      <ol class="steps">
        <li v-for="(c, i) in CYCLE" :key="c.name">
          <span class="n mono">{{ String(i + 1).padStart(2, '0') }}</span>
          <h3>{{ c.name }}</h3>
          <p>{{ c.body }}</p>
        </li>
      </ol>
      <ul class="outcomes">
        <li v-for="o in OUTCOMES" :key="o.name" :class="o.tone"><b>{{ o.name }}</b><span>{{ o.body }}</span></li>
      </ul>
      <p class="back mono">↺ 每一步都回到「核对」，直到目标达成</p>
    </div>

    <div class="grid">
      <div class="layers" role="img" aria-label="分层的物理智能：Agent 决定做什么，专用技能决定怎么做，物理系统核对实际发生了什么">
        <div v-for="(l, j) in LAYERS" :key="l.who" class="layer" :class="`l${j}`">
          <p class="who">{{ l.who }}</p>
          <p class="motto" lang="en">{{ l.en }}</p>
          <p class="items"><span v-for="it in l.items" :key="it">{{ it }}</span></p>
        </div>
      </div>
      <div class="text">
        <h3>大脑不必控制每一个电机</h3>
        <p>不同任务需要不同层级的智能。Agentic 大脑负责长期目标、任务分解、工具选择、设备协调和异常恢复；控制器、运动规划器、VLA 或 WAM 负责具体动作；独立的执行系统负责参数限制、动作互锁、授权检查和停止机制。</p>
        <p>走向真实设备时，同样的操作意图经设备适配器映射到真实硬件的控制接口。投入使用之前，先检查模型与硬件是否一致、坐标是否标定、状态是否可信、互锁是否真实生效。</p>
        <p class="pull">Agent 可以提出行动，但不能绕过物理设备的安全边界。</p>
        <StatusList :items="STATUS" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.pl .part { font-size: 11px; letter-spacing: .08em; color: var(--accent-ink); }
.pl-section-head .part + h2 { margin-top: 14px; }

.cycle { margin-top: 56px; padding: 26px 28px 20px; border-radius: 18px; border: 1px solid var(--line); background: color-mix(in srgb, var(--surface) 72%, transparent); box-shadow: var(--shadow-1); }
.steps { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }
.steps li { position: relative; padding-top: 14px; border-top: 2px solid var(--line-2); }
.steps li:nth-child(n + 5) { border-top-color: var(--accent); }
.steps .n { font-size: 10.5px; color: var(--faint); }
.pl .steps h3 { margin-top: 6px; font-size: 18px; }
.pl .steps p { margin-top: 6px; font-size: 13px; line-height: 1.7; color: var(--ink-2); }
.outcomes { list-style: none; margin: 20px 0 0; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.outcomes li { display: flex; align-items: baseline; gap: 10px; padding: 10px 14px; border-radius: 10px; border: 1px solid var(--line); background: var(--surface); font-size: 13.5px; color: var(--ink-2); }
.outcomes b { font-weight: 600; }
.outcomes .ok b { color: var(--ok); }
.outcomes .warn b { color: var(--warn); }
.outcomes .muted b { color: var(--muted); }
.pl .back { margin-top: 12px; font-size: 11px; color: var(--faint); text-align: right; }

.grid { margin-top: 72px; display: grid; grid-template-columns: 1fr 1.05fr; gap: 56px; align-items: start; }
.layers { position: sticky; top: 96px; display: flex; flex-direction: column; gap: 10px; }
.layer { padding: 20px 22px; border-radius: 14px; border: 1px solid var(--line); background: var(--surface); box-shadow: var(--shadow-1); }
.layer.l0 { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); background: color-mix(in srgb, var(--accent) 7%, var(--surface)); }
.layer.l2 { background: var(--surface-2); }
.pl .who { font-family: var(--font-mono); font-size: 11px; letter-spacing: .08em; color: var(--faint); }
.l0 .who { color: var(--accent-ink); }
.pl .motto { margin-top: 6px; font-family: var(--font-display); font-weight: 600; font-size: 19px; line-height: 1.4; color: var(--ink); }
.pl .items { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px; }
.items span { padding: 3px 9px; border-radius: 6px; border: 1px solid var(--line-2); font-size: 12.5px; color: var(--ink-2); }
.pl .text h3 { font-size: clamp(22px, 2.4vw, 28px); }
.pl .text p { margin-top: 14px; font-size: 15.5px; line-height: 1.9; color: var(--ink-2); }
.pl .text .pull { margin-top: 22px; padding-left: 18px; border-left: 2px solid var(--accent); font-family: var(--font-display); font-weight: 600; font-size: 19px; line-height: 1.6; color: var(--ink); }
.text :deep(.sl) { margin-top: 24px; }

@media (max-width: 1000px) {
  .steps { grid-template-columns: repeat(3, 1fr); row-gap: 20px; }
  .outcomes { grid-template-columns: 1fr; }
  .grid { grid-template-columns: 1fr; gap: 36px; }
  .layers { position: static; }
}
@media (max-width: 620px) {
  .cycle { padding: 20px 18px; }
  .steps { grid-template-columns: 1fr 1fr; }
  .pl .back { text-align: left; }
}
</style>
