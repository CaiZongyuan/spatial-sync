<script setup lang="ts">
// Part one, World Building: from a picture to a computable world the agent can rehearse in.
import { withBase } from 'vitepress'
import StatusList from './StatusList.vue'
import type { Status } from './story'

const LAYERS = [
  { name: '外观', body: '照片、视频、CAD 或一段描述；从素材库检索并组合已有模型，补建缺失的设备。' },
  { name: '几何', body: '尺寸、坐标与布局。看不到的尺寸记为假设，再用规格资料、人工确认或测量修正。' },
  { name: '物理', body: '质量、碰撞、关节、限位与接触。让试管能被夹起，让离心机盖能被锁住。' },
  { name: '设备语义', body: '每台设备能做什么、此刻怎样、什么条件下不能做。Agent 读的就是这一层。' },
]

const STATUS: { s: Status; t: string }[] = [
  { s: 'delivered', t: '场景工厂：Agent 检索素材、调用 Blender 组装场景' },
  { s: 'delivered', t: '场景与素材的不可变版本、验证报告与 Newton 预览' },
  { s: 'delivered', t: 'Newton 与 Synthetic 仿真会话、机械臂路径预览' },
  { s: 'planned', t: '从照片与视频重建场景，补全任务所需的物理属性' },
  { s: 'planned', t: 'Sim2Sim：在多个物理后端上验证同一策略' },
]
</script>

<template>
  <section id="world-building" class="pl-section">
    <p class="pl-eyebrow pl-rule"><span>04 · World Building</span></p>
    <div class="head">
      <div class="pl-section-head">
        <p class="part mono">Lab World · 01</p>
        <h2><span class="nb">从一张图片，</span><span class="nb">到一个可计算、可仿真的世界</span></h2>
        <p class="pl-section-lede">Physical AI 首先需要一个能理解、能交互的物理环境，而建造它不该永远依赖昂贵、漫长的人工建模。Lab World 让 Agent 成为世界的构建者：调用 Blender 等工程工具，把真实实验室一层层搭成可以检查、修复、版本化和复用的场景资产。</p>
      </div>
      <p class="pull">一个看起来正确的世界，不一定是一个物理上正确的世界。</p>
    </div>

    <figure class="pl-figure hero-fig">
      <img :src="withBase('/images/landing/build-world.webp')" alt="同一张实验台从左到右逐渐变化：真实照片、灰色三维模型，再到带碰撞体、关节轴和坐标系的仿真模型" width="1600" height="900" loading="lazy" decoding="async" />
    </figure>
    <ol class="layers">
      <li v-for="(l, i) in LAYERS" :key="l.name">
        <span class="n mono">{{ String(i + 1).padStart(2, '0') }}</span>
        <h3>{{ l.name }}</h3>
        <p>{{ l.body }}</p>
      </li>
    </ol>

    <div class="sim">
      <figure class="pl-figure">
        <img :src="withBase('/images/landing/scenario-robotics.webp')" alt="一台机械臂把试管插进离心机，旁边一排半透明的同款机械臂在并行练习同一个动作" width="1600" height="900" loading="lazy" decoding="async" />
        <figcaption><span class="mono" lang="en">Simulate Before Acting</span>同一个动作，在成百上千个并行环境里先练。</figcaption>
      </figure>
      <div>
        <h3>建好的世界，先让仿真承担试错成本</h3>
        <p>物理世界里的试错有代价：一次抓取失败可能损坏样本，一次错误操作可能让实验重来。在仿真里，同一个任务可以反复执行，物体位置可以随机化，不同策略可以在相同条件下比较，系统地寻找失败边界。同一任务还可以在不同物理后端上验证，看清某一种仿真的特殊假设是否在影响结果。</p>
        <p class="pull small">仿真的目标不是证明机器人永远不会失败，而是在真实部署之前，更早发现能够被发现的问题。</p>
        <StatusList :items="STATUS" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.head { display: grid; grid-template-columns: 1.4fr 1fr; gap: 56px; align-items: end; }
.pl .part { font-size: 11px; letter-spacing: .08em; color: var(--accent-ink); }
.head .pl-section-head h2 { margin-top: 14px; }
.pl .pull { padding-left: 18px; border-left: 2px solid var(--accent); font-family: var(--font-display); font-weight: 600; font-size: 22px; line-height: 1.6; color: var(--ink); }
.pl .pull.small { margin-top: 22px; font-size: 18px; }
.hero-fig { margin-top: 48px; }
.layers { list-style: none; margin: 28px 0 0; padding: 0; display: grid; grid-template-columns: repeat(4, 1fr); gap: 0 28px; }
.layers li { padding-top: 18px; border-top: 1px solid var(--line-2); }
.layers li:last-child { border-top-color: var(--accent); }
.layers .n { font-size: 11px; color: var(--faint); }
.pl .layers h3 { margin-top: 10px; font-size: 22px; }
.pl .layers p { margin-top: 8px; font-size: 14px; line-height: 1.8; color: var(--ink-2); }
.sim { margin-top: 88px; display: grid; grid-template-columns: 1fr 1fr; gap: 56px; align-items: start; }
.pl .sim h3 { font-size: clamp(22px, 2.4vw, 28px); line-height: 1.35; }
.pl .sim h3 + p { margin-top: 14px; font-size: 15.5px; line-height: 1.9; color: var(--ink-2); }
.sim :deep(.sl) { margin-top: 24px; }

@media (max-width: 1000px) {
  .head { grid-template-columns: 1fr; gap: 28px; }
  .layers { grid-template-columns: 1fr 1fr; gap: 28px; }
  .sim { grid-template-columns: 1fr; gap: 32px; margin-top: 64px; }
}
@media (max-width: 620px) {
  .pl .pull { font-size: 18px; }
  .layers { grid-template-columns: 1fr; gap: 22px; }
}
</style>
