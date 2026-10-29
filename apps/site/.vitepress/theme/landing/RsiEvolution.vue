<script setup lang="ts">
// Part three, RSI evolution: one bounded improvement round, the two layers it
// improves, Real2Sim, and the rules that make a better version trustworthy
// (docs/spec/LAB_RSI_SPEC_V1.md).
import { withBase } from 'vitepress'
import StatusList from './StatusList.vue'
import type { Status } from './story'

const ROUND = [
  { title: '读证据', body: '实验记录、记分板、失败的录制与真实轨迹。' },
  { title: '提假设', body: '提出一个可证伪的假设，只改一处。', gate: '人批准训练计划' },
  { title: '做评估', body: '候选版本在独立评估集上逐题运行、打分。' },
  { title: '判胜负', body: '满足接受规则才算更好，否则回滚。', gate: '人批准提升' },
  { title: '写回去', body: '新版本成为参照，下一轮站在它上面。' },
]

const LAYERS = [
  { name: 'Agent 层', what: '操作实验室的 Agent', change: '任务分解、技能、工具使用、异常处理（Harness）', suite: '实验室操作任务', cost: 'LLM Token' },
  { name: 'Physical Skill 层', what: '驱动机器人的策略与程序', change: '机器人策略、控制程序、任务参数、仿真环境', suite: '机器人回合', cost: 'GPU 小时' },
]

const RULES = [
  { title: '接受规则', body: '候选成功率的 Wilson 95% 置信区间下界，必须高于当前版本；Agent 层每题至少运行 3 次、平均分严格更高。' },
  { title: '改进者看不到考题', body: '负责改进的一方读不到私有评分条件和测试集；一旦发现泄露，候选回滚，这一轮标记为污染。' },
  { title: '成本算得清', body: '每一轮用掉的 Token、GPU 小时和时间都记账；闭环有预算上限，超预算要人批准。' },
]

const STATUS: { s: Status; t: string }[] = [
  { s: 'delivered', t: '每次运行的录制、调用记录与拒绝原因' },
  { s: 'delivered', t: '训练、评估、接受、拒绝与回滚的工程链路' },
  { s: 'delivered', t: 'Agent Harness 版本化与私有评分的实验室任务' },
  { s: 'planned', t: '学习式接触抓取策略达到正式提升标准' },
  { s: 'planned', t: 'Real2Sim：用真实运行校准仿真，生成新的测试与训练任务' },
]
</script>

<template>
  <section id="rsi" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>06 · RSI 进化</span></p>
    <div class="pl-section-head">
      <p class="part mono">Lab World · 03</p>
      <h2><span class="nb">每一次尝试，</span><span class="nb">都让下一次更好</span></h2>
      <p class="pl-section-lede">RSI（Recursive Self-Improvement，递归自我改进）：系统改进自己的一部分，用外部反馈判断好坏，只保留更好的版本，下一轮站在这一轮的结果上。软件里，外部反馈是测试和基准；在物理世界里，它只能来自真实的设备和实验。Lab World 把 RSI 落成一个有边界、可审计的工程循环。</p>
    </div>

    <ol class="round" aria-label="一轮改进的五个步骤">
      <li v-for="(s, i) in ROUND" :key="s.title">
        <span class="n mono">{{ String(i + 1).padStart(2, '0') }}</span>
        <h3>{{ s.title }}</h3>
        <p>{{ s.body }}</p>
        <span v-if="s.gate" class="gate"><i>人</i>{{ s.gate }}</span>
      </li>
    </ol>
    <p class="again mono">↺ 回到 01，开始下一轮</p>

    <div class="layers">
      <article v-for="l in LAYERS" :key="l.name">
        <p class="kick mono">{{ l.name }}</p>
        <h3>{{ l.what }}</h3>
        <dl>
          <div><dt>改进什么</dt><dd>{{ l.change }}</dd></div>
          <div><dt>用什么评估</dt><dd>{{ l.suite }}</dd></div>
          <div><dt>成本记在</dt><dd>{{ l.cost }}</dd></div>
        </dl>
      </article>
    </div>

    <div class="real">
      <figure class="pl-figure">
        <img :src="withBase('/images/landing/twin.webp')" alt="左边是由靛蓝色光线构成的数字孪生工作站，右边是真实的机械臂与离心机，两道光弧在它们之间双向流动" width="1600" height="800" loading="lazy" decoding="async" />
        <figcaption><span class="mono" lang="en">Sim2Real · Real2Sim</span>同一个循环的两个方向。</figcaption>
      </figure>
      <div>
        <h3>现实，是最终的事实</h3>
        <p>现实不会永远按仿真的假设运行：物体偏移、夹爪误差、传感器噪声、设备延迟、真实的摩擦。平台保存真实轨迹、相机观察、关节状态、设备事件和失败时刻，分析预测与结果的差异。物理模型不准，先校准物理模型；模型合理而策略不稳，就在修正后的环境里训练新策略。这就是 Real2Sim。</p>
        <p class="pull">自我改进不是让 AI 无限制地修改自己，而是让每次改进都能被提出、检验、接受或否决。</p>
        <StatusList :items="STATUS" />
      </div>
    </div>

    <div class="rules">
      <div v-for="r in RULES" :key="r.title">
        <h3>{{ r.title }}</h3>
        <p>{{ r.body }}</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.pl .part { font-size: 11px; letter-spacing: .08em; color: var(--accent-ink); }
.pl-section-head .part + h2 { margin-top: 14px; }

.round { list-style: none; margin: 56px 0 0; padding: 0; display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
.round li { position: relative; display: flex; flex-direction: column; align-items: flex-start; padding: 20px 18px 18px; border: 1px solid var(--line); border-radius: 14px; background: color-mix(in srgb, var(--surface) 70%, transparent); box-shadow: var(--shadow-1); }
.round li + li::before { content: ''; position: absolute; left: -13px; top: 36px; width: 14px; height: 1px; background: var(--line-2); }
.round .n { font-size: 11px; color: var(--faint); }
.pl .round h3 { margin-top: 10px; font-size: 20px; }
.pl .round p { margin-top: 8px; font-size: 13.5px; line-height: 1.7; color: var(--ink-2); }
.gate { margin-top: 14px; display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--accent-ink); }
.gate i { font-style: normal; display: grid; place-items: center; width: 20px; height: 20px; border-radius: 5px; background: var(--accent); color: var(--on-accent); font-size: 11px; }
.pl .again { margin-top: 14px; font-size: 11.5px; color: var(--faint); text-align: right; }

.layers { margin-top: 56px; display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.layers article { padding: 28px; border-radius: 16px; border: 1px solid var(--line); background: var(--surface); box-shadow: var(--shadow-1); }
.pl .kick { font-size: 11px; letter-spacing: .08em; color: var(--accent-ink); }
.pl .layers h3 { margin-top: 10px; font-size: 24px; }
dl { margin: 18px 0 0; display: flex; flex-direction: column; }
dl div { display: grid; grid-template-columns: 96px 1fr; gap: 16px; padding: 12px 0; border-top: 1px solid var(--line); }
dt { font-size: 12.5px; color: var(--faint); }
dd { margin: 0; font-size: 14.5px; line-height: 1.7; color: var(--ink-2); }

.real { margin-top: 88px; display: grid; grid-template-columns: 1fr 1fr; gap: 56px; align-items: start; }
.pl .real h3 { font-size: clamp(22px, 2.4vw, 28px); }
.pl .real h3 + p { margin-top: 14px; font-size: 15.5px; line-height: 1.9; color: var(--ink-2); }
.pl .real .pull { margin-top: 22px; padding-left: 18px; border-left: 2px solid var(--accent); font-family: var(--font-display); font-weight: 600; font-size: 19px; line-height: 1.6; color: var(--ink); }
.real :deep(.sl) { margin-top: 24px; }

.rules { margin-top: 72px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 32px; }
.rules > div { padding-top: 20px; border-top: 1px solid var(--line-2); }
.pl .rules h3 { font-size: 20px; }
.pl .rules p { margin-top: 10px; font-size: 14.5px; line-height: 1.8; color: var(--ink-2); }

@media (max-width: 1000px) {
  .round { grid-template-columns: 1fr 1fr; }
  .round li + li::before { display: none; }
  .layers, .rules, .real { grid-template-columns: 1fr; }
  .real { gap: 32px; margin-top: 64px; }
}
@media (max-width: 620px) {
  .round { grid-template-columns: 1fr; }
  .layers article { padding: 22px; }
  dl div { grid-template-columns: 84px 1fr; }
}
</style>
