<script setup lang="ts">
// Current project views and platform screenshots; the expandable transcript is
// a separate Synthetic run (lab-demo@3) captured on 2026-10-21.
import { withBase } from 'vitepress'

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
      <h2><span class="nb">看得见的场景，</span><span class="nb">用得上的工作台</span></h2>
      <p class="pl-section-lede">从一座细胞培养工作站，到一整个实验室。场景、素材和设备能力汇入同一个平台，人和 Agent 可以在这里查看环境、提交计划、操作设备，并追溯每一次调用的结果。</p>
    </div>

    <figure class="pl-figure scene-hero">
      <a :href="withBase('/images/product/workcell-overview.webp')" target="_blank" rel="noopener" aria-label="查看细胞培养 Workcell 大图">
        <img :src="withBase('/images/product/workcell-overview.webp')" alt="细胞培养 Workcell 的平台空间视图：玻璃围护内的机械臂、培养箱、移液设备和工作台" width="1600" height="1000" loading="lazy" decoding="async" />
      </a>
      <figcaption><span class="mono">细胞培养 Workcell · 平台空间视图</span>机器人、仪器、轨道与实验台，放进同一个可查看的空间。</figcaption>
    </figure>
    <div class="platform-head">
      <h3>一个入口，连接整个实验室</h3>
      <p>侧栏连接场景、素材、计划、训练与运行记录；空间视图与场景信息在同一张工作台上展开。</p>
    </div>
    <figure class="pl-figure platform-shot">
      <a :href="withBase('/images/product/platform-dark.webp')" target="_blank" rel="noopener" aria-label="查看深色平台完整截图">
        <img :src="withBase('/images/product/platform-dark.webp')" alt="Lab World 深色模式完整界面，包含导航侧栏、细胞培养 Workcell 空间视图、场景版本和操作面板" width="1796" height="1018" loading="lazy" decoding="async" />
      </a>
      <figcaption><span class="mono">Lab World · 深色模式 · 实际截图</span>场景视图、固定版本与设备信息，集中在同一个入口。</figcaption>
    </figure>
    <figure class="pl-figure platform-shot asset-shot">
        <a :href="withBase('/images/product/assets-nvidia-dark.webp')" target="_blank" rel="noopener" aria-label="查看 NVIDIA 在线素材库完整截图">
          <img :src="withBase('/images/product/assets-nvidia-dark.webp')" alt="深色模式素材库，选中在线素材与 NVIDIA SimReady 来源筛选，卡片展示对应素材及预览图" width="1796" height="1018" loading="lazy" decoding="async" />
        </a>
        <figcaption><span class="mono">素材库 · NVIDIA SimReady</span>在线目录按来源筛选，浏览素材、预览与元数据。</figcaption>
    </figure>

    <details class="run-evidence">
      <summary>查看一次实际运行：计划、审批与互锁<span aria-hidden="true">＋</span></summary>
      <div class="evidence-body">
        <p>下面是 Synthetic 仿真实验室里一次独立运行的命令行节选。计划先等待批准；机械臂完成搬运后，关盖被互锁拒绝。计划修订、重新获批，机械臂回到安全位置，离心机才继续执行。</p>
        <figure class="term">
          <div class="term-bar"><span class="mono">lab-demo@3 · Synthetic · 2026-10-21 · 节选</span></div>
          <pre><code><span v-for="(l, i) in RUN" :key="i" class="line" :class="l.kind"><b v-if="l.kind === 'cmd'">$ </b>{{ l.text }}
</span></code></pre>
        </figure>
        <p class="evidence-note">这段记录展示计划、前置条件、审批与结果检查的执行链路。场景图片展示各自的空间与设备布局；完整机器人任务、真实硬件实验与学习效果，需要各自的运行验证。</p>
      </div>
    </details>
  </section>
</template>

<style scoped>
.scene-hero { margin-top: 44px; }
.scene-hero img { width: 100%; aspect-ratio: 8 / 5; object-fit: cover; background: #171716; }
.asset-shot { margin-top: 28px; }
.pl-figure a { display: block; border-radius: 12px; overflow: hidden; }
.pl-figure a:focus-visible { outline: 2px solid var(--accent); outline-offset: 5px; }
.platform-head { margin: 64px 0 24px; }
.pl .platform-head h3 { font-size: clamp(22px, 2.4vw, 28px); }
.pl .platform-head p { margin-top: 12px; max-width: 700px; font-size: 15px; line-height: 1.85; color: var(--ink-2); }
.platform-shot a { border: 1px solid var(--line-2); box-shadow: var(--shadow-1); }
.platform-shot img { width: 100%; height: auto; }
.run-evidence { margin-top: 40px; border: 1px solid var(--line); border-radius: 14px; background: var(--surface); overflow: hidden; }
summary { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 20px 24px; font-size: 15px; color: var(--ink-2); cursor: pointer; list-style: none; }
summary::-webkit-details-marker { display: none; }
summary span { color: var(--accent-ink); font-size: 22px; }
details[open] summary span { rotate: 45deg; }
.evidence-body { padding: 0 24px 24px; }
.pl .evidence-body p { font-size: 14px; line-height: 1.85; color: var(--ink-2); }
.term { margin: 20px 0 0; border: 1px solid var(--term-line); border-radius: 10px; overflow: hidden; background: var(--term-bg); }
.term-bar { padding: 12px 18px; border-bottom: 1px solid var(--term-line); color: var(--term-dim); font-size: 11px; }
pre { margin: 0; padding: 18px 20px; overflow-x: auto; font-family: var(--font-mono); font-size: 12.5px; line-height: 1.85; color: var(--term-ink); }
.line { white-space: pre-wrap; overflow-wrap: anywhere; }
.line b { font-weight: 400; color: var(--term-dim); }
.line.note { color: var(--term-dim); }
.line.ok { color: var(--term-ok); }
.line.bad { color: var(--term-bad); }
.pl .evidence-note { margin-top: 18px; color: var(--muted); }
@media (max-width: 700px) {
  .platform-head { margin-top: 44px; }
  summary { padding: 18px; font-size: 14px; }
  .evidence-body { padding: 0 18px 18px; }
  pre { padding: 16px; font-size: 11.5px; }
}
</style>
