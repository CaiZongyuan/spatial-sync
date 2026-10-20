<script setup lang="ts">
// PROTOTYPE (#87) Variant C · 分章小场景: an editorial essay; each chapter owns a small looping figure.
import { withBase } from 'vitepress'
import Vignette from './Vignette.vue'
import StatusChip from './StatusChip.vue'
import Inventory from './Inventory.vue'
import PlanPanel from './PlanPanel.vue'
import HudStrip from './HudStrip.vue'
import AttemptGrid from './AttemptGrid.vue'
import TrainChart from './TrainChart.vue'
import ResultChart from './ResultChart.vue'
import LoopRing from './LoopRing.vue'
import ScaleStats from './ScaleStats.vue'
import GateToast from './GateToast.vue'
import StatusLedger from './StatusLedger.vue'
import StartBlock from './StartBlock.vue'
import { CHAPTERS, CONSOLE, HERO, STATUS_LABEL } from './story'

defineOptions({ name: 'VariantC' })

const C = Object.fromEntries(CHAPTERS.map((c) => [c.id, c]))
const fig = (id: string, i: number) => `图 ${i} · ${C[id].kicker} · ${STATUS_LABEL[C[id].status]}`
const repo = 'https://github.com/CaiZongyuan/spatial-sync'
</script>

<template>
  <article class="vc pl">
    <header class="vc-head">
      <p class="pl-eyebrow vc-rule"><span>{{ HERO.eyebrow }}</span></p>
      <h1><span class="nb">会自我改进的</span><span class="nb">实验室</span></h1>
      <p class="lede">{{ HERO.lede }}</p>
      <p class="meta">
        <a :href="withBase('/guide/getting-started')">快速开始 →</a>
        <a :href="repo" target="_blank" rel="noreferrer">GitHub →</a>
        <span>{{ HERO.note }}</span>
      </p>
    </header>

    <figure class="vc-fig vc-fig--hero">
      <Vignette class="frame" :from="0" :to="11" framing="film" :hold="1.6" />
      <figcaption><span class="mono">图 0 · 一座实验室亮起来</span>agent 连上平台的那一刻，读到每一台设备。</figcaption>
    </figure>

    <section class="vc-sec vc-quote">
      <p class="kick"><span class="mono">{{ C.goal.n }}</span>{{ C.goal.kicker }}<StatusChip :status="C.goal.status" /></p>
      <blockquote>「{{ CONSOLE[0].text }}。」</blockquote>
      <p class="body">{{ C.goal.title }}。这是人在整个流程里唯一一次打字；接下来的每一步，都由 agent 自己决定。</p>
    </section>

    <section class="vc-sec">
      <div class="txt">
        <p class="kick"><span class="mono">{{ C.sense.n }}</span>{{ C.sense.kicker }}<StatusChip :status="C.sense.status" /></p>
        <h2>{{ C.sense.title }}</h2>
        <p class="body">{{ C.sense.body }}</p>
      </div>
      <figure class="vc-fig">
        <Vignette class="frame" :from="5" :to="11" chapter="sense" v-slot="{ t }"><Inventory class="inset" :t="t" /></Vignette>
        <figcaption><span class="mono">{{ fig('sense', 1) }}</span>同一个 Catalog，人和 agent 看到的是同一份事实。</figcaption>
      </figure>
    </section>

    <section class="vc-sec flip">
      <div class="txt">
        <p class="kick"><span class="mono">{{ C.plan.n }}</span>{{ C.plan.kicker }}<StatusChip :status="C.plan.status" /></p>
        <h2>{{ C.plan.title }}</h2>
        <p class="body">{{ C.plan.body }}</p>
      </div>
      <figure class="vc-fig">
        <Vignette class="frame" :from="11" :to="17" chapter="plan" v-slot="{ t }"><PlanPanel class="inset" :t="t" /></Vignette>
        <figcaption><span class="mono">{{ fig('plan', 2) }}</span>虚线是 TCP 轨迹，半透明的是预演中的机械臂。</figcaption>
      </figure>
    </section>

    <section class="vc-sec">
      <div class="txt">
        <p class="kick"><span class="mono">{{ C.run.n }}</span>{{ C.run.kicker }}<StatusChip :status="C.run.status" /></p>
        <h2>{{ C.run.title }}</h2>
        <p class="body">{{ C.run.body }}</p>
      </div>
      <figure class="vc-fig">
        <Vignette class="frame" :from="17" :to="26" chapter="run" v-slot="{ t }"><HudStrip class="inset inset--bottom" :t="t" /></Vignette>
        <figcaption><span class="mono">{{ fig('run', 3) }}</span>时间快进；人工介入仍是 0。</figcaption>
      </figure>
    </section>

    <section class="vc-sec flip">
      <div class="txt">
        <p class="kick"><span class="mono">{{ C.find.n }}</span>{{ C.find.kicker }}<StatusChip :status="C.find.status" /></p>
        <h2>{{ C.find.title }}</h2>
        <p class="body">{{ C.find.body }}</p>
      </div>
      <figure class="vc-fig">
        <Vignette class="frame" :from="26" :to="31" chapter="find" v-slot="{ t }"><AttemptGrid class="inset" :t="t" /></Vignette>
        <figcaption><span class="mono">{{ fig('find', 4) }}</span>失败不是噪声，是下一轮改进的线索。</figcaption>
      </figure>
    </section>

    <section class="vc-sec">
      <div class="txt">
        <p class="kick"><span class="mono">{{ C.evolve.n }}</span>{{ C.evolve.kicker }}<StatusChip :status="C.evolve.status" /></p>
        <h2>{{ C.evolve.title }}</h2>
        <p class="body">{{ C.evolve.body }}</p>
      </div>
      <figure class="vc-fig">
        <Vignette class="frame" :from="31" :to="41" chapter="evolve" sim :labels="false" v-slot="{ t }">
          <TrainChart class="inset" :t="t" />
          <div class="gate-inset"><GateToast :t="t" /></div>
        </Vignette>
        <figcaption><span class="mono">{{ fig('evolve', 5) }}</span>每个半透明工位都是一个并行的仿真环境。</figcaption>
      </figure>
    </section>

    <section class="vc-sec flip">
      <div class="txt">
        <p class="kick"><span class="mono">{{ C.result.n }}</span>{{ C.result.kicker }}<StatusChip :status="C.result.status" /></p>
        <h2>{{ C.result.title }}</h2>
        <p class="body">{{ C.result.body }}</p>
      </div>
      <figure class="vc-fig">
        <Vignette class="frame" :from="42" :to="48" chapter="result" v-slot="{ t }"><ResultChart class="inset" :t="t" /></Vignette>
        <figcaption><span class="mono">{{ fig('result', 6) }}</span>新策略接回之后，同一步不再失败。</figcaption>
      </figure>
    </section>

    <section class="vc-sec vc-loop">
      <div class="txt">
        <p class="kick"><span class="mono">{{ C.loop.n }}</span>{{ C.loop.kicker }}<StatusChip :status="C.loop.status" /></p>
        <h2>{{ C.loop.title }}</h2>
        <p class="body">{{ C.loop.body }}</p>
      </div>
      <figure class="vc-fig vc-fig--diagram">
        <LoopRing :t="52" :size="380" bare />
        <figcaption><span class="mono">{{ fig('loop', 7) }}</span>两处「人」是仅有的人工关口。</figcaption>
      </figure>
    </section>

    <section class="vc-scale">
      <div class="vc-scale-txt">
        <p class="kick"><span class="mono">{{ C.scale.n }}</span>{{ C.scale.kicker }}<StatusChip :status="C.scale.status" /></p>
        <h2>{{ C.scale.title }}</h2>
        <p class="body">{{ C.scale.body }}</p>
      </div>
      <figure class="vc-fig vc-fig--bleed">
        <Vignette class="frame" :from="52" :to="62" framing="film" city :labels="false" :hold="2.4" v-slot="{ t }"><ScaleStats class="inset inset--scale" :t="t" /></Vignette>
        <figcaption><span class="mono">图 8 · 规模 · 愿景</span>一处学到的策略与技能，沿着光点流向所有实验室。</figcaption>
      </figure>
    </section>

    <StatusLedger />
    <StartBlock />
  </article>
</template>

<style scoped>
.vc { background: var(--ground); }
.vc-head { max-width: 1080px; margin: 0 auto; padding: 152px 24px 0; }
.vc-rule { display: flex; align-items: center; gap: 16px; }
.vc-rule::after { content: ''; flex: 1; height: 1px; background: var(--line); }
.vc-head h1 { margin-top: 36px; font-size: clamp(52px, 8.4vw, 116px); line-height: 1.04; letter-spacing: -0.01em; }
.lede { margin-top: 32px; max-width: 640px; font-size: 19px; line-height: 1.8; color: var(--ink-2); }
.meta { margin-top: 28px; display: flex; flex-wrap: wrap; gap: 8px 24px; font-size: 13.5px; color: var(--faint); }
.meta a { color: var(--ink); border-bottom: 1px solid var(--line-2); padding-bottom: 2px; }
.meta a:hover { border-color: var(--ink); }

.vc-fig { margin: 0; }
.vc-fig .frame { width: 100%; aspect-ratio: 4 / 3; border-radius: 14px; border: 1px solid var(--line); background:
  radial-gradient(ellipse 70% 60% at 50% 45%, color-mix(in srgb, var(--surface-2) 75%, transparent), transparent 75%), var(--surface); box-shadow: var(--shadow-1); }
.vc-fig figcaption { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 13px; line-height: 1.6; color: var(--muted); }
.vc-fig figcaption .mono { font-size: 11px; color: var(--faint); letter-spacing: .04em; }
.vc-fig--hero { max-width: 1180px; margin: 72px auto 0; padding: 0 24px; }
.vc-fig--hero .frame { aspect-ratio: 21 / 10; }

.vc-sec { max-width: 1240px; margin: 0 auto; padding: 120px 24px 0; display: grid; grid-template-columns: 0.66fr 1.34fr; gap: 72px; align-items: center; }
.vc-sec.flip { grid-template-columns: 1.34fr 0.66fr; }
.vc-sec.flip .txt { order: 2; }
.kick { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--muted); }
.kick .mono { font-size: 12px; color: var(--faint); }
.txt h2 { margin-top: 14px; font-size: clamp(28px, 3.2vw, 40px); line-height: 1.25; }
.body { margin-top: 16px; font-size: 16px; line-height: 1.85; color: var(--ink-2); }

.vc-quote { grid-template-columns: 1fr; max-width: 900px; gap: 0; }
.vc-quote blockquote { margin: 22px 0 0; padding: 0; border: 0; font-family: var(--font-display); font-size: clamp(28px, 4vw, 48px); line-height: 1.4; color: var(--ink); }
.vc-quote .body { max-width: 620px; color: var(--muted); }

.inset { position: absolute; right: -28px; bottom: -36px; width: 256px; z-index: 2; box-shadow: var(--shadow-2); }
.flip .inset { right: auto; left: -28px; }
.inset--bottom { width: auto; }
.inset--scale { bottom: 28px; left: max(24px, calc(50% - 566px)); right: auto; width: 230px; }
.gate-inset { position: absolute; left: 16px; top: 16px; z-index: 2; }
.vc-sec .vc-fig { position: relative; }
.vc-sec .vc-fig figcaption { padding-right: 240px; }
.vc-sec.flip .vc-fig figcaption { padding-right: 0; padding-left: 240px; }

.vc-loop .vc-fig--diagram { display: flex; flex-direction: column; align-items: center; padding: 24px 0; border-radius: 14px; border: 1px solid var(--line); background: var(--surface); box-shadow: var(--shadow-1); }
.vc-fig--diagram figcaption { justify-content: center; }

.vc-scale { margin-top: 140px; }
.vc-scale-txt { max-width: 760px; margin: 0 auto; padding: 0 24px; text-align: center; }
.vc-scale-txt .kick { justify-content: center; }
.vc-scale-txt h2 { margin-top: 14px; font-size: clamp(34px, 5vw, 60px); line-height: 1.18; }
.vc-scale-txt .body { margin-left: auto; margin-right: auto; max-width: 600px; }
.vc-fig--bleed { margin-top: 48px; }
.vc-fig--bleed .frame { aspect-ratio: auto; height: min(78vh, 760px); border-radius: 0; border-left: 0; border-right: 0; box-shadow: none; background: var(--ground); }
.vc-fig--bleed figcaption { max-width: 1180px; margin-left: auto; margin-right: auto; padding: 0 24px; }

@media (max-width: 900px) {
  .vc-head { padding: 108px 16px 0; }
  .vc-head h1 { margin-top: 24px; font-size: 48px; }
  .lede { margin-top: 20px; font-size: 16px; }
  .vc-fig--hero { padding: 0 16px; margin-top: 40px; }
  .vc-fig--hero .frame { aspect-ratio: 4 / 3.4; }
  .vc-sec, .vc-sec.flip { grid-template-columns: 1fr; gap: 24px; padding: 72px 16px 0; }
  .vc-sec.flip .txt { order: 0; }
  .body { font-size: 15px; }
  .inset, .flip .inset { left: 12px; right: 12px; bottom: -24px; width: auto; }
  .vc-sec .vc-fig figcaption, .vc-sec.flip .vc-fig figcaption { padding: 32px 0 0; }
  .gate-inset { display: none; }
  .vc-scale { margin-top: 88px; }
  .vc-scale-txt { padding: 0 16px; }
  .vc-fig--bleed .frame { height: 70vh; }
}
</style>
