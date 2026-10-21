<script setup lang="ts">
// How one improvement round works (docs/spec/LAB_RSI_SPEC_V1.md): the loop, the two
// layers it improves, and the rules that make a result trustworthy.
import StatusChip from './StatusChip.vue'

const STEPS = [
  { n: '01', title: '读记录', body: '读实验记录、记分板和失败的录制。' },
  { n: '02', title: '提假设', body: '提出一个可证伪的假设，只改一处。', gate: '人批准训练计划' },
  { n: '03', title: '做评估', body: '候选版本在评估集上逐题运行、打分。' },
  { n: '04', title: '判胜负', body: '满足接受规则才算更好，否则回滚。', gate: '人批准提升' },
  { n: '05', title: '写回去', body: '新版本成为基准，结论写进实验记录。' },
]

const LAYERS = [
  { name: 'Agent 层', what: '操作实验室的 agent', fixed: '模型与思考等级', change: 'Harness：平台 skill、AGENTS.md 里的一段指引、工具与 MCP 配置', suite: '实验室操作任务', cost: 'LLM Token' },
  { name: '具身层', what: '驱动机器人的 Controller', fixed: '机器人本体与场景版本', change: '策略、设备程序、奖励、示范数据、控制参数', suite: '机器人回合', cost: 'GPU 小时' },
]

const RULES = [
  { title: '接受规则', body: '具身层：候选成功率的 Wilson 95% 置信区间下界，必须高于当前版本的成功率。Agent 层：训练题平均分必须严格更高，每题至少运行 3 次。' },
  { title: '改进者看不到考题', body: '负责改进的 Optimizer 读不到私有评分条件和测试集。每次工具调用都留在 Trace 里；一旦发现泄露，候选回滚，计划标记为污染。' },
  { title: '成本算得清', body: '每一轮用掉的 Token、GPU 小时和时间都记进成本中心；闭环有预算上限，超预算要人批准。' },
]
</script>

<template>
  <section id="how" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>闭环怎么转</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">每一轮，</span><span class="nb">只保留更好的版本</span></h2>
      <p class="pl-section-lede">Lab World 用同一套框架改进两类对象。每一轮都是：一个可证伪的假设，一处有边界的改动，在评估集上打分，满足接受规则才成为新版本。分数、成本和每个版本都可以追溯。</p>
      <StatusChip status="planned" />
    </div>

    <ol class="loop" aria-label="一轮改进的五个步骤">
      <li v-for="s in STEPS" :key="s.n">
        <span class="n mono">{{ s.n }}</span>
        <h3>{{ s.title }}</h3>
        <p>{{ s.body }}</p>
        <span v-if="s.gate" class="gate"><i>人</i>{{ s.gate }}</span>
      </li>
    </ol>
    <p class="back mono">↺ 回到 01，开始下一轮</p>

    <div class="layers">
      <article v-for="l in LAYERS" :key="l.name">
        <p class="kick mono">{{ l.name }}</p>
        <h3>{{ l.what }}</h3>
        <dl>
          <div><dt>保持不变</dt><dd>{{ l.fixed }}</dd></div>
          <div><dt>可以改</dt><dd>{{ l.change }}</dd></div>
          <div><dt>用什么评估</dt><dd>{{ l.suite }}</dd></div>
          <div><dt>成本记在</dt><dd>{{ l.cost }}</dd></div>
        </dl>
      </article>
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
.pl-section-head :deep(.pl-chip) { margin-top: 18px; }
.loop { list-style: none; margin: 56px 0 0; padding: 0; display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; counter-reset: step; }
.loop li { position: relative; display: flex; flex-direction: column; align-items: flex-start; padding: 20px 18px 18px; border: 1px solid var(--line); border-radius: 14px; background: color-mix(in srgb, var(--surface) 70%, transparent); box-shadow: var(--shadow-1); }
.loop li + li::before { content: ''; position: absolute; left: -13px; top: 36px; width: 14px; height: 1px; background: var(--line-2); }
.loop .n { font-size: 11px; color: var(--faint); }
.pl .loop h3 { margin-top: 10px; font-size: 20px; }
.pl .loop p { margin-top: 8px; font-size: 13.5px; line-height: 1.7; color: var(--ink-2); }
.gate { margin-top: 14px; display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--accent-ink); }
.gate i { font-style: normal; display: grid; place-items: center; width: 20px; height: 20px; border-radius: 5px; background: var(--accent); color: var(--on-accent); font-size: 11px; }
.pl .back { margin-top: 14px; font-size: 11.5px; color: var(--faint); text-align: right; }

.layers { margin-top: 72px; display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.layers article { padding: 28px; border-radius: 16px; border: 1px solid var(--line); background: var(--surface); box-shadow: var(--shadow-1); }
.pl .kick { font-size: 11px; letter-spacing: .08em; color: var(--accent-ink); }
.pl .layers h3 { margin-top: 10px; font-size: 26px; }
dl { margin: 20px 0 0; display: flex; flex-direction: column; }
dl div { display: grid; grid-template-columns: 96px 1fr; gap: 16px; padding: 12px 0; border-top: 1px solid var(--line); }
dt { font-size: 12.5px; color: var(--faint); }
dd { margin: 0; font-size: 14.5px; line-height: 1.7; color: var(--ink-2); }

.rules { margin-top: 56px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 32px; }
.rules > div { padding-top: 20px; border-top: 1px solid var(--line-2); }
.pl .rules h3 { font-size: 20px; }
.pl .rules p { margin-top: 10px; font-size: 14.5px; line-height: 1.8; color: var(--ink-2); }

@media (max-width: 1000px) {
  .loop { grid-template-columns: 1fr 1fr; }
  .loop li + li::before { display: none; }
  .layers, .rules { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .loop { grid-template-columns: 1fr; }
  .layers article { padding: 22px; }
  dl div { grid-template-columns: 84px 1fr; }
}
</style>
