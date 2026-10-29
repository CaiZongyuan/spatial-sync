<script setup lang="ts">
// Why the next frontier for agents is the physical world, and why it is buildable now.
import { withBase } from 'vitepress'

const CURVES = [
  { title: 'Agent 开始掌握专业工程工具', body: '先进模型可以根据图片和设计要求，通过 Blender、CAD 等专业软件创建、修改和检查三维资产。原本大量依赖人工的场景构建，正在变成 Agent 可以协调完成的工程流程。' },
  { title: '物理仿真与机器人学习基础设施成熟', body: 'GPU 加速的物理引擎、开放的场景描述标准、多后端训练框架和大规模并行仿真，让构建环境、训练策略、验证机器人在不同条件下的行为，都变得更高效。' },
  { title: '机器人智能走向通用的决策与行动', body: 'VLA、World Action Model 和直接调用工具的 Agentic Control，让机器人不再只执行写死的动作序列，而是结合视觉、任务目标和环境反馈去行动。' },
]
</script>

<template>
  <section id="frontier" class="pl-section" tabindex="-1">
    <p class="pl-eyebrow pl-rule"><span>01 · The Next Frontier</span></p>
    <div class="grid">
      <div class="essay">
        <h2><span class="nb">AI 的下一个边界，</span><span class="nb">是物理世界</span></h2>
        <p>过去几年，AI 从回答问题走到理解复杂任务，从生成内容走到调用工具，从辅助编程走到持续完成软件工程。它正在从一个回答问题的模型，变成能够自主执行任务的 Agent。</p>
        <p>在数字世界里，这套能力已经有清晰的基础设施：Agent 读取文件、运行代码、调用 API、观察结果、发现错误，再修改自己的行动。</p>
        <p>物理世界不一样。一台机器人不是软件里的一个函数，它有真实的质量、关节、传感器和运动约束；一次实验也不只是一段代码，它涉及仪器、样本、物理过程、时间顺序，以及无法忽略的安全条件。</p>
        <p class="claim">Physical AI 需要的不只是更聪明的模型，而是一个连接模型与物理世界的完整系统：让 AI 知道世界是什么样的、能做什么、行动会造成什么影响，以及如何判断行动是否成功。</p>
      </div>
      <figure class="pl-figure">
        <img :src="withBase('/images/landing/frontier.webp')" alt="一块悬浮的玻璃屏幕里流出靛蓝色光线，越过实验台，连到机械臂、试管架、离心机和培养箱" width="1536" height="1024" loading="lazy" decoding="async" />
        <figcaption><span class="mono">图 · 走出屏幕</span>Agent 的能力，从代码延伸到真实设备。</figcaption>
      </figure>
    </div>

    <blockquote class="quote">
      <p><span class="nb">在软件里，</span><span class="nb">错误通常意味着一次失败的调用；</span><span class="nb">在物理世界里，</span><span class="nb">错误可能意味着真实的碰撞、</span><span class="nb">设备损坏或不可逆的实验损失。</span></p>
    </blockquote>

    <div class="now">
      <p class="now-head"><span lang="en">Why Now</span> · 三条技术曲线，正在同一时间交汇</p>
      <ol>
        <li v-for="(c, i) in CURVES" :key="c.title">
          <span class="n mono">{{ String(i + 1).padStart(2, '0') }}</span>
          <h3>{{ c.title }}</h3>
          <p>{{ c.body }}</p>
        </li>
      </ol>
      <p class="then">过去，每一种机器人、每一座实验室、每一个任务，都要建一套专用软件。三条曲线交汇之后，第一次可以<strong>让 Agent 参与物理世界的构建、验证、执行与学习全过程</strong>，并在此之上建立一套可以持续复用的基础平台。</p>
    </div>
  </section>
</template>

<style scoped>
.grid { margin-top: 32px; display: grid; grid-template-columns: 1fr 1.05fr; gap: 64px; align-items: start; }
.pl .essay h2 { font-size: clamp(32px, 3.8vw, 50px); line-height: 1.18; }
.pl .essay p { margin-top: 20px; font-size: 16.5px; line-height: 1.9; color: var(--ink-2); }
.pl .essay h2 + p { margin-top: 28px; }
.pl .essay .claim { margin-top: 28px; padding-left: 18px; border-left: 2px solid var(--accent); font-family: var(--font-display); font-weight: 600; font-size: 19px; line-height: 1.7; color: var(--ink); }
.pl-figure { margin-top: 8px; }

.quote { margin: 112px auto 0; padding: 0; border: 0; max-width: 980px; text-align: center; }
.pl .quote p { font-family: var(--font-display); font-weight: 600; font-size: clamp(24px, 3vw, 38px); line-height: 1.55; color: var(--ink); text-wrap: balance; }

.now { margin-top: 112px; }
.pl .now-head { font-family: var(--font-mono); font-size: 11.5px; letter-spacing: .08em; color: var(--faint); text-transform: uppercase; }
.now ol { list-style: none; margin: 20px 0 0; padding: 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 32px; }
.now li { padding-top: 20px; border-top: 1px solid var(--line-2); }
.now .n { font-size: 12px; color: var(--faint); }
.pl .now h3 { margin-top: 12px; font-size: 22px; line-height: 1.35; }
.pl .now li p { margin-top: 10px; font-size: 14.5px; line-height: 1.8; color: var(--ink-2); }
.pl .then { margin-top: 48px; max-width: 780px; font-size: 17px; line-height: 1.85; color: var(--ink-2); }
.then strong { font-weight: 600; color: var(--ink); }

@media (max-width: 1000px) {
  .grid { grid-template-columns: 1fr; gap: 40px; }
  .now ol { grid-template-columns: 1fr; gap: 24px; }
}
@media (max-width: 620px) {
  .pl .essay p { font-size: 15.5px; }
  .pl .essay .claim { font-size: 17px; }
  .quote { margin-top: 72px; text-align: left; }
  .now { margin-top: 72px; }
  .pl .then { font-size: 15.5px; }
}
</style>
