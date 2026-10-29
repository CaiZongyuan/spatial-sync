<script setup lang="ts">
// The home page. A sticky 3D lab plays the storyboard as the reader scrolls, or
// by itself on autoplay; one click skips it. Long-form sections follow the
// investor narrative: the frontier and why now, what Physical AI is, Lab World's
// three parts and one section for each, one workcell, what runs today, the first
// market, the ecosystem, compounding and the roadmap, then every chapter as a
// still, the blog and the long-term vision.
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import StageCanvas from './StageCanvas.vue'
import AgentConsole from './AgentConsole.vue'
import InsightPanel from './InsightPanel.vue'
import HudStrip from './HudStrip.vue'
import GateToast from './GateToast.vue'
import StatusChip from './StatusChip.vue'
import Frontier from './Frontier.vue'
import PhysicalAI from './PhysicalAI.vue'
import Platform from './Platform.vue'
import WorldBuilding from './WorldBuilding.vue'
import AgenticBrain from './AgenticBrain.vue'
import RsiEvolution from './RsiEvolution.vue'
import Workcell from './Workcell.vue'
import RealRun from './RealRun.vue'
import StatusLedger from './StatusLedger.vue'
import Market from './Market.vue'
import Ecosystem from './Ecosystem.vue'
import Compounding from './Compounding.vue'
import Roadmap from './Roadmap.vue'
import ChapterIndex from './ChapterIndex.vue'
import BlogLatest from './BlogLatest.vue'
import Vision from './Vision.vue'
import { CHAPTERS, DURATION, HERO, PARTS, PART_LABEL, chapterAt, chapterIndex, clamp, labT, lerp, smooth } from './story'
import './landing.css'

// Scroll budget, in viewport heights: the hero, then each film second, then a hold.
const HERO_SCREENS = 0.75
const PER_SECOND = 0.15
const END_SCREENS = 0.6
const STILL_W = 960
const STILL_H = 600
// Autoplay runs the film in real time; the hero fades out four times faster.
const HERO_SPEED = 4
const SCROLL_KEYS = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '])

const track = ref<HTMLElement>()
const stage = ref<InstanceType<typeof StageCanvas>>()
const index = ref<InstanceType<typeof ChapterIndex>>()
const ctl = ref<HTMLButtonElement>()
const vh = ref(900)
const vw = ref(1440)
const t = ref(0)
const heroP = ref(0)
const reduced = ref(false)
const stills = shallowRef<string[]>([])
const playing = ref(false)
let target = 0
// Film time while autoplaying, negative while the hero is still fading out.
let playT = 0
// Focus moves into the film controls once the hero (which held it) fades and turns inert.
let focusControls = false
let raf = 0
let last = 0
let shot = 0
let io: IntersectionObserver | null = null
let mo: MutationObserver | null = null

const trackH = computed(() => vh.value * (1 + HERO_SCREENS + DURATION * PER_SECOND + END_SCREENS))
const chapter = computed(() => chapterAt(t.value))
const ci = computed(() => chapterIndex(t.value))
const lab = computed(() => labT(t.value))
// The rail groups its chapters under the three parts of Lab World.
const railParts = PARTS.map((p) => ({ ...p, span: CHAPTERS.filter((c) => c.part === p.id).reduce((a, c) => a + c.end - c.start, 0) }))
const heroO = computed(() => 1 - smooth(heroP.value))
const narrow = computed(() => vw.value < 900)
const offsetX = computed(() => (narrow.value ? 0 : lerp(0.07, 0.22, heroO.value)))
const offsetY = computed(() => (narrow.value ? lerp(-0.02, -0.24, heroO.value) : 0))
const local = computed(() => clamp((t.value - chapter.value.start) / (chapter.value.end - chapter.value.start)))
const ended = computed(() => t.value >= DURATION - 0.05)

function measure() {
  vh.value = window.innerHeight
  vw.value = window.innerWidth
}

function read() {
  if (!track.value) return
  const y = -track.value.getBoundingClientRect().top
  heroP.value = clamp(y / (HERO_SCREENS * vh.value))
  const tt = clamp((y - HERO_SCREENS * vh.value) / (PER_SECOND * vh.value), 0, DURATION)
  // Reduced motion holds each chapter on its key frame instead of scrubbing.
  target = reduced.value ? chapterAt(tt).key : tt
}

function onResize() {
  measure()
  read()
}

// The page scroll is the one film clock: autoplay and chapter jumps both move it.
function filmTop() {
  return track.value!.getBoundingClientRect().top + window.scrollY
}

function scrollFor(time: number) {
  return filmTop() + (HERO_SCREENS + time * PER_SECOND) * vh.value
}

function jump(i: number) {
  if (!track.value) return
  const time = i === 0 ? 0 : CHAPTERS[i].start + 0.2
  if (playing.value) playT = time
  else window.scrollTo({ top: scrollFor(time), behavior: reduced.value ? 'auto' : 'smooth' })
}

function play() {
  if (!track.value) return
  const now = (window.scrollY - filmTop()) / (PER_SECOND * vh.value) - HERO_SCREENS / PER_SECOND
  playT = now >= DURATION - 0.05 ? 0 : Math.max(now, -HERO_SCREENS / PER_SECOND)
  playing.value = true
  focusControls = Boolean(document.activeElement?.closest('.hero'))
}

function toggle() {
  if (playing.value) playing.value = false
  else play()
}

function skip() {
  playing.value = false
  const el = document.getElementById('frontier')
  if (!el) return
  window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY, behavior: reduced.value ? 'auto' : 'smooth' })
  el.focus({ preventScroll: true })
}

// The closing call to action plays the film again from its first chapter.
function replay() {
  if (!track.value) return
  playT = 0
  playing.value = true
  focusControls = true
  window.scrollTo(0, scrollFor(0))
}

// Any scroll input from the reader takes the film back from autoplay.
function interrupt(e: Event) {
  if (!playing.value || (e.target as Element | null)?.closest?.('[data-film-control]')) return
  if (e instanceof KeyboardEvent && !SCROLL_KEYS.has(e.key)) return
  playing.value = false
}

const idle = () => new Promise<void>((resolve) => {
  if ('requestIdleCallback' in window) requestIdleCallback(() => resolve(), { timeout: 400 })
  else setTimeout(resolve, 16)
})

// One still per idle slot, so the hero never stutters; a theme change reshoots.
async function shoot() {
  const run = ++shot
  const out: string[] = []
  for (const c of CHAPTERS) {
    await idle()
    if (run !== shot) return
    const [url] = stage.value?.stills([c.key], STILL_W, STILL_H) ?? []
    if (!url) return
    out.push(url)
    stills.value = [...out]
  }
}

function watchIndex() {
  const el = index.value?.$el as HTMLElement | undefined
  if (!el) return
  io = new IntersectionObserver(([e]) => {
    if (!e.isIntersecting) return
    io?.disconnect()
    shoot()
  }, { rootMargin: '150% 0px' })
  io.observe(el)
}

onMounted(() => {
  reduced.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  measure()
  read()
  t.value = target
  window.addEventListener('scroll', read, { passive: true })
  window.addEventListener('resize', onResize)
  for (const type of ['wheel', 'touchstart', 'pointerdown', 'keydown']) window.addEventListener(type, interrupt, { passive: true })
  mo = new MutationObserver(() => { if (stills.value.length) setTimeout(shoot, 50) })
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  last = performance.now()
  const tick = (now: number) => {
    const dt = Math.min(0.1, (now - last) / 1000)
    last = now
    if (playing.value) {
      playT = Math.min(DURATION, playT + dt * (playT < 0 ? HERO_SPEED : 1))
      window.scrollTo(0, scrollFor(playT))
      if (playT >= DURATION) playing.value = false
    }
    if (focusControls && heroO.value < 0.5) {
      focusControls = false
      ctl.value?.focus({ preventScroll: true })
    }
    // Ease toward the scroll position so wheel steps read as camera moves.
    const k = reduced.value ? 1 : 1 - Math.exp(-dt * 6)
    t.value += (target - t.value) * k
    if (Math.abs(target - t.value) < 0.0005) t.value = target
    raf = requestAnimationFrame(tick)
  }
  raf = requestAnimationFrame(tick)
})

onBeforeUnmount(() => {
  shot++
  cancelAnimationFrame(raf)
  io?.disconnect()
  mo?.disconnect()
  window.removeEventListener('scroll', read)
  window.removeEventListener('resize', onResize)
  for (const type of ['wheel', 'touchstart', 'pointerdown', 'keydown']) window.removeEventListener(type, interrupt)
})
</script>

<template>
  <div class="pl">
    <section ref="track" class="film" :style="{ height: trackH + 'px' }">
      <div class="sticky">
        <StageCanvas ref="stage" class="stage" :t="t" :offset-x="offsetX" :offset-y="offsetY" :reduced="reduced" @ready="watchIndex" />
        <div class="veil" :style="{ opacity: 0.8 + 0.2 * heroO }" />

        <header class="hero" :inert="heroO <= 0.5 || undefined" :style="{ opacity: heroO, transform: `translateY(${-28 * (1 - heroO)}px)`, pointerEvents: heroO > 0.5 ? 'auto' : 'none' }">
          <p class="pl-eyebrow">{{ HERO.eyebrow }}</p>
          <h1><span class="nb" lang="en">Lab World</span><span class="nb">迈向物理 AI</span></h1>
          <p class="tagline" lang="en">{{ HERO.tagline }}</p>
          <p class="lede">{{ HERO.lede }}</p>
          <ul class="parts" aria-label="Lab World 的三个核心构成">
            <li v-for="p in PARTS" :key="p.id"><a :href="p.href"><b>{{ p.name }}</b><span>{{ p.line }}</span></a></li>
          </ul>
          <div class="actions" data-film-control>
            <button class="pl-btn pl-btn--primary" @click="play"><svg viewBox="0 0 16 16"><path d="M5 3.2v9.6L12.6 8z" fill="currentColor" /></svg>自动播放</button>
            <button class="pl-btn" @click="skip">跳过动画<svg viewBox="0 0 16 16"><path d="M8 3v10m0 0 4-4m-4 4L4 9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg></button>
          </div>
          <p class="note">也可以向下滚动，按自己的节奏看。</p>
        </header>

        <div class="copy" :style="{ opacity: 1 - heroO }">
          <Transition name="swap" mode="out-in">
            <div :key="chapter.id">
              <p class="part mono">{{ PART_LABEL[chapter.part] }}</p>
              <p class="kicker"><span class="mono">{{ chapter.n }}</span><span>{{ chapter.kicker }}</span><StatusChip :status="chapter.status" /></p>
              <h2>{{ chapter.title }}</h2>
              <p class="body">{{ chapter.body }}</p>
            </div>
          </Transition>
          <div class="local"><i :style="{ transform: `scaleX(${local})` }" /></div>
        </div>

        <aside class="instruments" :style="{ opacity: 1 - heroO }">
          <AgentConsole :t="t" :limit="narrow ? 3 : 6" />
          <InsightPanel v-if="!narrow" :t="t" />
        </aside>

        <div class="gate"><GateToast :t="lab" /></div>

        <HudStrip v-if="!narrow" class="hud" :t="lab" :style="{ opacity: 1 - heroO }" />

        <div class="controls" data-film-control :inert="heroO >= 0.5 || undefined" :style="{ opacity: 1 - heroO, pointerEvents: heroO < 0.5 ? 'auto' : 'none' }">
          <button ref="ctl" class="ctl" :aria-label="playing ? '暂停' : '自动播放'" :aria-pressed="playing" @click="toggle">
            <template v-if="playing"><svg viewBox="0 0 16 16"><path d="M5 3.5v9M11 3.5v9" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" /></svg></template><template v-else><svg viewBox="0 0 16 16"><path d="M5 3.2v9.6L12.6 8z" fill="currentColor" /></svg></template>
          </button>
          <div class="rail-wrap">
          <div class="rail-parts" aria-hidden="true">
            <span v-for="p in railParts" :key="p.id" :class="{ on: chapter.part === p.id }" :style="{ flexGrow: p.span }">{{ p.name }}</span>
          </div>
          <nav class="rail" aria-label="章节">
            <button v-for="(c, i) in CHAPTERS" :key="c.id" :class="{ on: i === ci, past: i < ci }" :style="{ flexGrow: c.end - c.start }" :aria-label="`第 ${c.n} 章：${c.kicker}`" :aria-current="i === ci ? 'step' : undefined" @click="jump(i)">
              <i /><span>{{ c.kicker }}</span>
            </button>
          </nav>
          </div>
          <button class="skip" @click="skip">{{ ended ? '继续阅读' : '跳过动画' }}<svg viewBox="0 0 16 16"><path d="M8 3v10m0 0 4-4m-4 4L4 9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg></button>
        </div>

        <div class="cue" :style="{ opacity: heroO }"><span>向下滚动</span><i /></div>
      </div>
    </section>

    <Frontier />
    <PhysicalAI />
    <Platform />
    <WorldBuilding />
    <AgenticBrain />
    <RsiEvolution />
    <Workcell />
    <RealRun />
    <StatusLedger />
    <Market />
    <Ecosystem />
    <Compounding />
    <Roadmap />
    <ChapterIndex ref="index" :stills="stills" @play="jump" />
    <BlogLatest />
    <Vision @replay="replay" />
  </div>
</template>

<style scoped>
.film { position: relative; }
/* The film is a viewport, so it stays smooth; the paper shows in the sections below. */
.sticky { position: sticky; top: 0; height: 100vh; height: 100svh; overflow: hidden; background: var(--ground); }
.stage { position: absolute; inset: 0; }
.veil { position: absolute; inset: 0; pointer-events: none; background:
  radial-gradient(ellipse 46% 80% at 0% 50%, color-mix(in srgb, var(--ground) 94%, transparent), color-mix(in srgb, var(--ground) 55%, transparent) 55%, transparent 100%),
  linear-gradient(180deg, color-mix(in srgb, var(--ground) 70%, transparent), transparent 18%, transparent 82%, color-mix(in srgb, var(--ground) 80%, transparent)); }

.hero { position: absolute; left: max(32px, 6vw); top: 50%; translate: 0 -50%; max-width: 600px; }
.hero h1 { margin-top: 18px; font-size: clamp(44px, 6.2vw, 84px); line-height: 1.08; }
.tagline { margin-top: 14px; font-family: var(--font-display); font-style: italic; font-size: 19px; line-height: 1.5; color: var(--muted); }
.lede { margin-top: 22px; font-size: 17px; line-height: 1.75; color: var(--ink-2); max-width: 520px; }
.parts { list-style: none; margin: 22px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; max-width: 560px; }
.parts a { display: flex; flex-direction: column; gap: 2px; padding: 9px 13px; border-radius: 10px; border: 1px solid var(--line-2); background: color-mix(in srgb, var(--surface) 72%, transparent); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); transition: border-color .2s var(--ease); }
.parts a:hover { border-color: var(--accent); }
.parts b { font-family: var(--font-display); font-weight: 600; font-size: 15px; color: var(--ink); }
.parts span { font-size: 11.5px; color: var(--muted); }
.actions { margin-top: 30px; display: flex; gap: 10px; flex-wrap: wrap; }
.note { margin-top: 18px; font-size: 12.5px; color: var(--faint); }

.copy { position: absolute; left: max(32px, 5vw); top: 50%; translate: 0 -50%; width: min(380px, 30vw); }
.part { margin-bottom: 10px; font-size: 11px; letter-spacing: .08em; color: var(--accent-ink); text-transform: uppercase; }
.kicker { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--muted); }
.kicker .mono { color: var(--faint); font-size: 12px; }
.copy h2 { margin-top: 14px; font-size: clamp(28px, 3vw, 40px); line-height: 1.22; }
.body { margin-top: 16px; font-size: 15px; line-height: 1.8; color: var(--ink-2); }
.local { margin-top: 26px; height: 2px; background: var(--line); border-radius: 1px; overflow: hidden; }
.local i { display: block; height: 100%; background: var(--accent); transform-origin: left; }
.swap-enter-active, .swap-leave-active { transition: opacity .35s var(--ease), transform .35s var(--ease); }
.swap-enter-from { opacity: 0; transform: translateY(10px); }
.swap-leave-to { opacity: 0; transform: translateY(-8px); }

.instruments { position: absolute; right: 24px; top: 84px; bottom: 112px; width: 330px; display: flex; flex-direction: column; gap: 10px; overflow: hidden; }
.gate { position: absolute; left: 50%; top: 92px; translate: -50% 0; z-index: 3; }
.hud { position: absolute; left: max(32px, 5vw); bottom: 28px; }
.controls { position: absolute; left: calc(max(32px, 5vw) + 420px); right: 24px; bottom: 28px; display: flex; align-items: center; gap: 14px; z-index: 2; }
.ctl { flex: none; width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center; border: 1px solid var(--line-2); background: color-mix(in srgb, var(--surface) 80%, transparent); color: var(--ink); cursor: pointer; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); transition: border-color .2s var(--ease); }
.ctl:hover { border-color: var(--ghost); }
.ctl svg { width: 14px; height: 14px; }
.skip { flex: none; display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 12px; border-radius: 8px; border: 1px solid var(--line-2); background: color-mix(in srgb, var(--surface) 80%, transparent); color: var(--ink-2); font: inherit; font-size: 12.5px; cursor: pointer; backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); transition: border-color .2s var(--ease), color .2s var(--ease); }
.skip:hover { border-color: var(--ghost); color: var(--ink); }
.skip svg { width: 13px; height: 13px; }
.rail-wrap { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.rail-parts { display: flex; gap: 4px; }
.rail-parts span { flex-basis: 0; min-width: 0; padding-left: 6px; border-left: 1px solid var(--line-2); font-family: var(--font-mono); font-size: 10px; letter-spacing: .06em; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: color .3s var(--ease), border-color .3s var(--ease); }
.rail-parts span.on { color: var(--accent-ink); border-left-color: var(--accent); }
.rail { display: flex; gap: 4px; }
.rail button { flex-basis: 0; min-width: 0; display: flex; flex-direction: column; gap: 8px; padding: 6px 0; background: none; border: 0; cursor: pointer; text-align: left; }
.rail i { display: block; height: 2px; border-radius: 1px; background: var(--line-2); transition: background .3s var(--ease); }
.rail span { font-size: 11px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: color .3s var(--ease); }
.rail .past i { background: var(--ghost); }
.rail .on i { background: var(--accent); }
.rail .on span { color: var(--ink); }
.cue { position: absolute; left: 50%; bottom: 28px; translate: -50% 0; display: flex; flex-direction: column; align-items: center; gap: 8px; font-size: 11.5px; color: var(--faint); pointer-events: none; }
.cue i { width: 1px; height: 36px; background: linear-gradient(var(--ghost), transparent); animation: drop 1.8s var(--ease) infinite; transform-origin: top; }
@keyframes drop { 0% { transform: scaleY(0); opacity: 1 } 70% { transform: scaleY(1); opacity: 1 } 100% { transform: scaleY(1); opacity: 0 } }

@media (max-width: 1180px) {
  .instruments { width: 290px; }
  .controls { left: max(32px, 5vw); right: 338px; bottom: 96px; }
}
@media (max-width: 900px) {
  .veil { background: linear-gradient(180deg, color-mix(in srgb, var(--ground) 92%, transparent) 0%, color-mix(in srgb, var(--ground) 40%, transparent) 34%, transparent 52%, transparent 66%, color-mix(in srgb, var(--ground) 92%, transparent) 88%); }
  .hero { left: 16px; right: 16px; top: 92px; translate: none; }
  .hero h1 { font-size: 40px; }
  .tagline { font-size: 15px; margin-top: 10px; }
  .lede { font-size: 15px; margin-top: 14px; }
  .parts { margin-top: 14px; gap: 6px; }
  .parts a { padding: 6px 10px; }
  .parts b { font-size: 13px; }
  .parts span { display: none; }
  .actions { margin-top: 20px; }
  .note { display: none; }
  .copy { left: 16px; right: 16px; top: 76px; translate: none; width: auto; }
  .copy h2 { font-size: 24px; margin-top: 10px; }
  .body { font-size: 13.5px; line-height: 1.7; margin-top: 8px; }
  .local { margin-top: 12px; }
  .instruments { left: 16px; right: 16px; top: auto; bottom: 60px; width: auto; }
  .gate { top: auto; bottom: 176px; }
  .controls { left: 16px; right: 16px; bottom: 12px; gap: 10px; }
  .ctl { width: 32px; height: 32px; }
  .rail span, .rail-parts { display: none; }
  .rail button { padding: 4px 0; }
  .cue { display: none; }
}
</style>
