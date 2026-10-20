<script setup lang="ts">
// PROTOTYPE (#87) Variant B · 短片: an autoplaying product film up top, chapters as stills below.
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { withBase } from 'vitepress'
import StageCanvas from './StageCanvas.vue'
import AgentConsole from './AgentConsole.vue'
import InsightPanel from './InsightPanel.vue'
import HudStrip from './HudStrip.vue'
import GateToast from './GateToast.vue'
import StatusChip from './StatusChip.vue'
import StatusLedger from './StatusLedger.vue'
import StartBlock from './StartBlock.vue'
import type { LabStage } from './stage'
import { CHAPTERS, DURATION, HERO, chapterAt, chapterIndex, clamp } from './story'

defineOptions({ name: 'VariantB' })

const t = ref(0)
const playing = ref(true)
const inView = ref(true)
const reduced = ref(false)
const stills = shallowRef<string[]>([])
const narrow = ref(false)
let stage: LabStage | null = null
let raf = 0
let last = 0
let mo: MutationObserver | null = null

const chapter = computed(() => chapterAt(Math.min(t.value, DURATION - 0.001)))
const ci = computed(() => chapterIndex(Math.min(t.value, DURATION - 0.001)))
const view = computed(() => Math.min(t.value, DURATION))

function seek(i: number) {
  t.value = reduced.value ? CHAPTERS[i].key : CHAPTERS[i].start + 0.01
}

function toggle() {
  if (t.value >= DURATION) t.value = 0
  playing.value = !playing.value
}

const film = ref<HTMLElement>()
function replay(i: number) {
  seek(i)
  playing.value = !reduced.value
  film.value?.scrollIntoView({ behavior: reduced.value ? 'auto' : 'smooth', block: 'center' })
}

function fill(i: number) {
  const c = CHAPTERS[i]
  return clamp((view.value - c.start) / (c.end - c.start))
}

function shoot() {
  if (!stage) return
  const out: string[] = []
  for (const c of CHAPTERS) out.push(stage.snapshot(c.key))
  stills.value = out
}

function onReady(s: LabStage) {
  stage = s
  setTimeout(shoot, 400)
}

onMounted(() => {
  reduced.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  narrow.value = window.innerWidth < 900
  window.addEventListener('resize', () => { narrow.value = window.innerWidth < 900 })
  if (reduced.value) { playing.value = false; t.value = CHAPTERS[0].key }
  last = performance.now()
  const tick = (now: number) => {
    const dt = Math.min(0.1, (now - last) / 1000)
    last = now
    if (playing.value && inView.value && !document.hidden) {
      t.value += dt
      if (t.value > DURATION + 1.6) t.value = 0
    }
    raf = requestAnimationFrame(tick)
  }
  raf = requestAnimationFrame(tick)
  mo = new MutationObserver(() => setTimeout(shoot, 50))
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
})

onBeforeUnmount(() => { cancelAnimationFrame(raf); mo?.disconnect() })
</script>

<template>
  <div class="vb pl">
    <header class="vb-head">
      <p class="pl-eyebrow">{{ HERO.eyebrow }}</p>
      <h1><span class="nb">会自我改进的</span><span class="nb">实验室</span></h1>
      <p class="lede">{{ HERO.lede }}</p>
      <div class="actions">
        <a class="pl-btn pl-btn--primary" :href="withBase('/guide/getting-started')">快速开始</a>
        <a class="pl-btn" href="#vb-chapters">逐章看</a>
      </div>
    </header>

    <section ref="film" class="vb-film">
      <div class="vb-window">
        <div class="vb-bar">
          <span class="dots"><i /><i /><i /></span>
          <span class="path mono">lab-rsi / goal-0007 / growth-screen</span>
          <span class="now"><span class="mono">{{ chapter.n }}</span>{{ chapter.kicker }}<StatusChip :status="chapter.status" /></span>
          <HudStrip v-if="!narrow" class="bar-hud" :t="view" bare />
        </div>
        <div class="vb-body">
          <StageCanvas class="vb-view" :t="view" :zoom="0.74" :offset-x="narrow ? 0 : 0.04" :reduced="reduced" @ready="onReady" @visible="(v) => (inView = v)">
            <div class="vb-gate"><GateToast :t="view" /></div>
            <Transition name="vb-cap" mode="out-in">
              <div :key="chapter.id" class="vb-caption">
                <h2>{{ chapter.title }}</h2>
                <p>{{ chapter.body }}</p>
              </div>
            </Transition>
          </StageCanvas>
          <aside class="vb-side">
            <AgentConsole :t="view" :limit="narrow ? 4 : 8" bare />
            <div class="side-insight"><InsightPanel :t="view" /></div>
          </aside>
        </div>
      </div>

      <div class="vb-scrub">
        <button class="play" :aria-label="playing ? '暂停' : '播放'" @click="toggle">
          <svg v-if="playing" viewBox="0 0 16 16"><path d="M5 3.5v9M11 3.5v9" /></svg>
          <svg v-else viewBox="0 0 16 16"><path d="M5 3.2v9.6L12.6 8z" class="fill" /></svg>
        </button>
        <div class="segs">
          <button v-for="(c, i) in CHAPTERS" :key="c.id" class="seg" :class="{ on: i === ci }" :style="{ flexGrow: c.end - c.start }" @click="seek(i)">
            <span class="bar"><i :style="{ transform: `scaleX(${fill(i)})` }" /></span>
            <span class="lbl"><span class="mono">{{ c.n }}</span> {{ c.kicker }}</span>
          </button>
        </div>
        <span class="time mono">{{ String(Math.floor(view)).padStart(2, '0') }} / {{ DURATION }} s</span>
      </div>
      <p class="vb-note">{{ HERO.note }}</p>
    </section>

    <section id="vb-chapters" class="vb-chapters">
      <article v-for="(c, i) in CHAPTERS" :key="c.id" class="row">
        <div class="num">{{ c.n }}</div>
        <div class="txt">
          <p class="kick">{{ c.kicker }} <StatusChip :status="c.status" /></p>
          <h3>{{ c.title }}</h3>
          <p class="body">{{ c.body }}</p>
          <button class="replay" @click="replay(i)">在短片中播放这一章 →</button>
        </div>
        <figure class="still">
          <img v-if="stills[i]" :src="stills[i]" :alt="`${c.kicker}：${c.title}`" />
          <div v-else class="ph" />
          <figcaption class="mono">t = {{ c.key.toFixed(1) }} s · 示意</figcaption>
        </figure>
      </article>
    </section>

    <StatusLedger />
    <StartBlock />
  </div>
</template>

<style scoped>
.vb { background: var(--ground); }
.vb-head { max-width: 860px; margin: 0 auto; padding: 128px 24px 0; text-align: center; }
.vb-head h1 { margin-top: 16px; font-size: clamp(40px, 5.4vw, 72px); line-height: 1.1; }
.lede { margin: 22px auto 0; max-width: 660px; font-size: 17px; line-height: 1.75; color: var(--ink-2); }
.actions { margin-top: 28px; display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }

.vb-film { max-width: 1280px; margin: 64px auto 0; padding: 0 24px; }
.vb-window { border: 1px solid var(--line); border-radius: 16px; background: var(--surface); box-shadow: var(--shadow-2), var(--bevel); overflow: hidden; }
.vb-bar { display: flex; align-items: center; gap: 16px; height: 46px; padding: 0 16px; border-bottom: 1px solid var(--line); background: color-mix(in srgb, var(--surface-2) 60%, var(--surface)); }
.dots { display: flex; gap: 6px; }
.dots i { width: 9px; height: 9px; border-radius: 50%; background: var(--line-2); }
.path { font-size: 11px; color: var(--faint); }
.now { margin-left: auto; display: inline-flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ink-2); }
.now .mono { font-size: 11px; color: var(--faint); }
.bar-hud { margin-left: 18px; }
.bar-hud :deep(.v) { font-size: 13px; }
.bar-hud :deep(.k) { display: none; }
.vb-body { display: grid; grid-template-columns: 1fr 340px; height: clamp(540px, 52vw, 680px); }
.vb-view { position: relative; background:
  radial-gradient(ellipse 70% 60% at 50% 45%, color-mix(in srgb, var(--surface-2) 70%, transparent), transparent 75%), var(--ground); }
.vb-gate { position: absolute; top: 18px; left: 50%; translate: -50% 0; z-index: 3; }
.vb-caption { position: absolute; left: 24px; bottom: 22px; max-width: 440px; padding: 16px 18px; border-radius: 12px; background: color-mix(in srgb, var(--surface) 82%, transparent); border: 1px solid var(--line); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); }
.vb-caption h2 { font-size: 22px; line-height: 1.3; }
.vb-caption p { margin-top: 6px; font-size: 13px; line-height: 1.7; color: var(--muted); }
.vb-cap-enter-active, .vb-cap-leave-active { transition: opacity .35s var(--ease), transform .35s var(--ease); }
.vb-cap-enter-from { opacity: 0; transform: translateY(8px); }
.vb-cap-leave-to { opacity: 0; }
.vb-side { border-left: 1px solid var(--line); padding: 4px 16px 16px; display: flex; flex-direction: column; gap: 14px; overflow: hidden; background: var(--surface); }
.side-insight :deep(.pl-panel) { box-shadow: none; background: var(--surface-2); }

.vb-scrub { display: flex; align-items: center; gap: 16px; margin-top: 18px; }
.play { flex: none; width: 36px; height: 36px; border-radius: 50%; border: 1px solid var(--line-2); background: var(--surface); display: grid; place-items: center; cursor: pointer; color: var(--ink); }
.play svg { width: 14px; height: 14px; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; fill: none; }
.play svg .fill { fill: currentColor; stroke: none; }
.segs { flex: 1; display: flex; gap: 4px; min-width: 0; }
.seg { flex-basis: 0; min-width: 0; display: flex; flex-direction: column; gap: 8px; padding: 8px 0 0; background: none; border: 0; cursor: pointer; text-align: left; }
.seg .bar { height: 3px; border-radius: 2px; background: var(--line-2); overflow: hidden; }
.seg .bar i { display: block; height: 100%; background: var(--ink-2); transform-origin: left; }
.seg.on .bar i { background: var(--accent); }
.seg .lbl { font-size: 11.5px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.seg .lbl .mono { font-size: 10.5px; }
.seg.on .lbl { color: var(--ink); }
.time { flex: none; font-size: 11px; color: var(--faint); }
.vb-note { margin-top: 12px; font-size: 12px; color: var(--faint); text-align: center; }

.vb-chapters { max-width: 1160px; margin: 0 auto; padding: 120px 24px 0; }
.row { display: grid; grid-template-columns: 88px 1fr 1.15fr; gap: 32px; align-items: center; padding: 40px 0; border-top: 1px solid var(--line); }
.num { align-self: start; font-family: var(--font-display); font-size: 44px; line-height: 1; color: var(--ghost); }
.kick { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--muted); }
.txt h3 { margin-top: 12px; font-size: 26px; line-height: 1.3; }
.txt .body { margin-top: 12px; font-size: 15px; line-height: 1.8; color: var(--ink-2); max-width: 440px; }
.replay { margin-top: 16px; padding: 0; background: none; border: 0; font-size: 13px; color: var(--accent-ink); cursor: pointer; }
.still { margin: 0; }
.still img, .ph { display: block; width: 100%; aspect-ratio: 16 / 10; object-fit: cover; border-radius: 12px; border: 1px solid var(--line); background: var(--surface-2); }
.still figcaption { margin-top: 8px; font-size: 10.5px; color: var(--faint); }

@media (max-width: 900px) {
  .vb-head { padding-top: 108px; }
  .vb-head h1 { font-size: 40px; }
  .lede { font-size: 15px; }
  .vb-film { padding: 0 16px; margin-top: 40px; }
  .path { display: none; }
  .vb-body { grid-template-columns: 1fr; height: auto; }
  .vb-view { height: 380px; }
  .vb-caption { left: 12px; right: 12px; bottom: 12px; padding: 12px 14px; }
  .vb-caption h2 { font-size: 18px; }
  .vb-caption p { display: none; }
  .vb-side { border-left: 0; border-top: 1px solid var(--line); max-height: 420px; }
  .seg .lbl { display: none; }
  .vb-chapters { padding: 72px 16px 0; }
  .row { grid-template-columns: 1fr; gap: 16px; padding: 32px 0; }
  .num { font-size: 32px; }
}
</style>
