<script setup lang="ts">
// One workcell, one real task: the three parts of Lab World carrying it from goal to a better next run.
import { withBase } from 'vitepress'
import { PART_LABEL, type Part } from './story'

const STEPS: { title: string; part: Part; body: string }[] = [
  { title: '理解', part: 'world', body: '实验室有什么设备、各有什么能力、样本在哪里、机械臂能否到达目标位置、离心机此刻是否允许操作。' },
  { title: '拆解', part: 'brain', body: '把任务拆成可检查的步骤，每一步写明目标状态和执行条件。' },
  { title: '预演', part: 'world', body: '真正动作之前，相关行为先在仿真里预演和验证。' },
  { title: '执行', part: 'brain', body: '每一步读取实际结果，再决定继续、等待、修订计划，还是请人介入。' },
  { title: '应对异常', part: 'brain', body: '离心机盖没能关上，就不假设命令已经成功；机械臂还停在危险区域，就先找出阻塞原因，满足约束后再试。' },
  { title: '留下证据', part: 'rsi', body: '从任务目标、场景版本、操作计划到每一个动作的结果，全部保留下来。' },
  { title: '下一次更好', part: 'rsi', body: '失败变成新的测试和训练任务；评估过线、人批准后，更好的版本接回流程。' },
]
</script>

<template>
  <section id="workcell" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>07 · Start With One Workcell</span></p>
    <div class="pl-section-head">
      <h2><span class="nb">一台机器人，</span><span class="nb">一组设备，</span><span class="nb">一个真实任务</span></h2>
    </div>

    <div class="grid">
      <div>
        <p class="task"><span class="label mono">任务</span>把试管架上的两支样本转运到离心机，按配平要求放入指定位置，在满足设备安全条件后启动离心，并把操作过程和结果完整记录下来。</p>
        <p class="lede">传统自动化要求工程师分别配置机器人、离心机、控制程序、状态监控和实验记录，再解决它们之间的协调。在 Lab World 里，使用者首先表达任务目标，而不是逐一编写底层操作。</p>
        <figure class="pl-figure">
          <img :src="withBase('/images/landing/workcell.webp')" alt="一座紧凑的工作站：中间是机械臂和发光的安全区，左边是试管架，右边打开的离心机里两个对称的位置亮着，表示配平" width="1536" height="1024" loading="lazy" decoding="async" />
          <figcaption><span class="mono">一个 Workcell</span>机械臂、试管架、离心机和一台观察相机；两个对称的转子位，就是配平。</figcaption>
        </figure>
      </div>
      <ol class="steps">
        <li v-for="(s, i) in STEPS" :key="s.title">
          <span class="n mono">{{ String(i + 1).padStart(2, '0') }}</span>
          <div>
            <h3>{{ s.title }}<span class="tag mono" :class="s.part">{{ PART_LABEL[s.part] }}</span></h3>
            <p>{{ s.body }}</p>
          </div>
        </li>
      </ol>
    </div>

    <p class="claim"><span class="nb">我们从这样一个有限、可验证的任务开始，</span><span class="nb">逐步构建可复用的物理智能。</span></p>
  </section>
</template>

<style scoped>
.grid { margin-top: 48px; display: grid; grid-template-columns: 1.05fr 1fr; gap: 64px; align-items: start; }
.pl .task { padding: 24px 26px; border-radius: 16px; border: 1px solid var(--line); background: var(--surface); box-shadow: var(--shadow-1); font-family: var(--font-display); font-weight: 600; font-size: 20px; line-height: 1.7; color: var(--ink); }
.label { display: block; margin-bottom: 10px; font-family: var(--font-mono); font-weight: 400; font-size: 11px; letter-spacing: .08em; color: var(--accent-ink); }
.pl .lede { margin-top: 24px; font-size: 16px; line-height: 1.85; color: var(--ink-2); }
.pl-figure { margin-top: 28px; }
.steps { list-style: none; margin: 0; padding: 0; position: relative; }
.steps::before { content: ''; position: absolute; left: 13px; top: 10px; bottom: 10px; width: 1px; background: var(--line-2); }
.steps li { position: relative; display: grid; grid-template-columns: 28px 1fr; gap: 18px; padding: 0 0 26px; }
.steps .n { position: relative; z-index: 1; display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; border: 1px solid var(--line-2); background: var(--ground); font-size: 10.5px; color: var(--muted); }
.steps .tag { margin-left: 10px; padding: 1px 7px; border-radius: 5px; border: 1px solid var(--line-2); font-family: var(--font-mono); font-weight: 400; font-size: 10.5px; letter-spacing: .02em; color: var(--muted); vertical-align: 2px; }
.steps .tag.rsi { color: var(--accent-ink); border-color: color-mix(in srgb, var(--accent) 45%, var(--line-2)); }
.steps li:nth-child(5) .n { border-color: color-mix(in srgb, var(--warn) 55%, var(--line-2)); color: var(--warn); }
.steps li:last-child { padding-bottom: 0; }
.steps li:last-child .n { border-color: var(--accent); color: var(--accent-ink); }
.pl .steps h3 { margin-top: 2px; font-size: 19px; }
.pl .steps p { margin-top: 6px; font-size: 14.5px; line-height: 1.8; color: var(--ink-2); }
.pl .claim { margin-top: 72px; font-family: var(--font-display); font-weight: 600; font-size: clamp(22px, 2.6vw, 30px); line-height: 1.5; color: var(--ink); text-wrap: balance; }

@media (max-width: 1000px) {
  .grid { grid-template-columns: 1fr; gap: 48px; }
}
@media (max-width: 620px) {
  .pl .task { font-size: 17px; padding: 20px; }
  .pl .lede { font-size: 15px; }
  .pl .claim { margin-top: 56px; }
}
</style>
