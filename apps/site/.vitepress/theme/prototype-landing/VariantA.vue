<script setup lang="ts">
// PROTOTYPE (#87) Variant A · 长镜头: one sticky 3D frame; scrolling scrubs the film.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { withBase } from 'vitepress'
import StageCanvas from './StageCanvas.vue'
import AgentConsole from './AgentConsole.vue'
import InsightPanel from './InsightPanel.vue'
import HudStrip from './HudStrip.vue'
import GateToast from './GateToast.vue'
import StatusChip from './StatusChip.vue'
import StatusLedger from './StatusLedger.vue'
import StartBlock from './StartBlock.vue'
import { CHAPTERS, DURATION, HERO, chapterAt, chapterIndex, clamp, lerp, smooth } from './story'

defineOptions({ name: 'VariantA' })

const HERO_SCREENS = 0.75
const PER_SECOND = 0.15
const END_SCREENS = 0.6

const track = ref<HTMLElement>()
const vh = ref(900)
const vw = ref(1440)
const t = ref(0)
const heroP = ref(0)
const reduced = ref(false)
let target = 0
let raf = 0
let last = 0

const trackH = computed(() => vh.value * (1 + HERO_SCREENS + DURATION * PER_SECOND + END_SCREENS))
const chapter = computed(() => chapterAt(t.value))
const ci = computed(() => chapterIndex(t.value))
const heroO = computed(() => 1 - smooth(heroP.value))
const narrow = computed(() => vw.value < 900)
const offsetX = computed(() => (narrow.value ? 0 : lerp(0.07, 0.22, heroO.value)))
const offsetY = computed(() => (narrow.value ? lerp(-0.02, -0.16, heroO.value) : 0))
const local = computed(() => clamp((t.value - chapter.value.start) / (chapter.value.end - chapter.value.start)))

function measure() {
  vh.value = window.innerHeight
  vw.value = window.innerWidth
}

function read() {
  if (!track.value) return
  const y = -track.value.getBoundingClientRect().top
  heroP.value = clamp(y / (HERO_SCREENS * vh.value))
  let tt = clamp((y - HERO_SCREENS * vh.value) / (PER_SECOND * vh.value), 0, DURATION)
  if (reduced.value) tt = chapterAt(tt).key
  target = tt
}

function jump(i: number) {
  if (!track.value) return
  const top = track.value.getBoundingClientRect().top + window.scrollY
  const c = CHAPTERS[i]
  const at = i === 0 ? HERO_SCREENS * vh.value : HERO_SCREENS * vh.value + (c.start + 0.2) * PER_SECOND * vh.value
  window.scrollTo({ top: top + at, behavior: reduced.value ? 'auto' : 'smooth' })
}

onMounted(() => {
  reduced.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  measure()
  read()
  t.value = target
  window.addEventListener('scroll', read, { passive: true })
  window.addEventListener('resize', () => { measure(); read() })
  last = performance.now()
  const tick = (now: number) => {
    const dt = Math.min(0.1, (now - last) / 1000)
    last = now
    const k = reduced.value ? 1 : 1 - Math.exp(-dt * 6)
    t.value += (target - t.value) * k
    if (Math.abs(target - t.value) < 0.0005) t.value = target
    raf = requestAnimationFrame(tick)
  }
  raf = requestAnimationFrame(tick)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  window.removeEventListener('scroll', read)
})
</script>

<template>
  <div class="va pl">
    <section ref="track" class="va-track" :style="{ height: trackH + 'px' }">
      <div class="va-sticky">
        <StageCanvas class="va-stage" :t="t" :offset-x="offsetX" :offset-y="offsetY" :reduced="reduced" />
        <div class="va-veil" :style="{ opacity: 0.8 + 0.2 * heroO }" />

        <header class="va-hero" :style="{ opacity: heroO, transform: `translateY(${-28 * (1 - heroO)}px)`, pointerEvents: heroO > 0.5 ? 'auto' : 'none' }">
          <p class="pl-eyebrow">{{ HERO.eyebrow }}</p>
          <h1><span class="nb">会自我改进的</span><span class="nb">实验室</span></h1>
          <p class="lede">{{ HERO.lede }}</p>
          <div class="actions">
            <button class="pl-btn pl-btn--primary" @click="jump(0)">看它怎么做到<svg viewBox="0 0 16 16"><path d="M8 3v10m0 0 4-4m-4 4L4 9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg></button>
            <a class="pl-btn" :href="withBase('/guide/getting-started')">快速开始</a>
          </div>
          <p class="note">{{ HERO.note }}</p>
        </header>

        <div class="va-copy" :style="{ opacity: 1 - heroO }">
          <Transition name="va-swap" mode="out-in">
            <div :key="chapter.id" class="va-copy-inner">
              <p class="kicker"><span class="mono">{{ chapter.n }}</span><span>{{ chapter.kicker }}</span><StatusChip :status="chapter.status" /></p>
              <h2>{{ chapter.title }}</h2>
              <p class="body">{{ chapter.body }}</p>
            </div>
          </Transition>
          <div class="va-local"><i :style="{ transform: `scaleX(${local})` }" /></div>
        </div>

        <aside class="va-instruments" :style="{ opacity: 1 - heroO }">
          <AgentConsole :t="t" :limit="narrow ? 3 : 6" />
          <InsightPanel v-if="!narrow" :t="t" />
        </aside>

        <div class="va-gate"><GateToast :t="t" /></div>

        <HudStrip v-if="!narrow" class="va-hud" :t="t" :style="{ opacity: 1 - heroO }" />

        <nav class="va-rail" :style="{ opacity: 1 - heroO }" aria-label="章节">
          <button v-for="(c, i) in CHAPTERS" :key="c.id" :class="{ on: i === ci, past: i < ci }" :style="{ flexGrow: c.end - c.start }" @click="jump(i)">
            <i /><span>{{ c.kicker }}</span>
          </button>
        </nav>

        <div class="va-cue" :style="{ opacity: heroO }"><span>向下滚动</span><i /></div>
      </div>
    </section>
    <section class="va-after">
      <StatusLedger />
      <StartBlock />
    </section>
  </div>
</template>

<style scoped>
.va { background: var(--ground); }
.va-track { position: relative; }
.va-sticky { position: sticky; top: 0; height: 100vh; height: 100svh; overflow: hidden; }
.va-stage { position: absolute; inset: 0; }
.va-veil { position: absolute; inset: 0; pointer-events: none; background:
  radial-gradient(ellipse 46% 80% at 0% 50%, color-mix(in srgb, var(--ground) 94%, transparent), color-mix(in srgb, var(--ground) 55%, transparent) 55%, transparent 100%),
  linear-gradient(180deg, color-mix(in srgb, var(--ground) 70%, transparent), transparent 18%, transparent 82%, color-mix(in srgb, var(--ground) 80%, transparent)); }

.va-hero { position: absolute; left: max(32px, 6vw); top: 50%; translate: 0 -50%; max-width: 600px; }
.va-hero h1 { margin-top: 18px; font-size: clamp(44px, 6.2vw, 84px); line-height: 1.08; }
.lede { margin-top: 22px; font-size: 17px; line-height: 1.75; color: var(--ink-2); max-width: 520px; }
.actions { margin-top: 30px; display: flex; gap: 10px; flex-wrap: wrap; }
.note { margin-top: 18px; font-size: 12px; color: var(--faint); }

.va-copy { position: absolute; left: max(32px, 5vw); top: 50%; translate: 0 -50%; width: min(380px, 30vw); }
.kicker { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--muted); }
.kicker .mono { color: var(--faint); font-size: 12px; }
.va-copy h2 { margin-top: 14px; font-size: clamp(28px, 3vw, 40px); line-height: 1.22; }
.body { margin-top: 16px; font-size: 15px; line-height: 1.8; color: var(--ink-2); }
.va-local { margin-top: 26px; height: 2px; background: var(--line); border-radius: 1px; overflow: hidden; }
.va-local i { display: block; height: 100%; background: var(--accent); transform-origin: left; }
.va-swap-enter-active, .va-swap-leave-active { transition: opacity .35s var(--ease), transform .35s var(--ease); }
.va-swap-enter-from { opacity: 0; transform: translateY(10px); }
.va-swap-leave-to { opacity: 0; transform: translateY(-8px); }

.va-instruments { position: absolute; right: 24px; top: 84px; bottom: 112px; width: 330px; display: flex; flex-direction: column; gap: 10px; overflow: hidden; }
.va-gate { position: absolute; left: 50%; top: 92px; translate: -50% 0; z-index: 3; }
.va-hud { position: absolute; left: max(32px, 5vw); bottom: 28px; }
.va-rail { position: absolute; left: calc(max(32px, 5vw) + 420px); right: 378px; bottom: 36px; display: flex; gap: 4px; }
.va-rail button { flex-basis: 0; min-width: 0; display: flex; flex-direction: column; gap: 8px; padding: 6px 0; background: none; border: 0; cursor: pointer; text-align: left; }
.va-rail i { display: block; height: 2px; border-radius: 1px; background: var(--line-2); transition: background .3s var(--ease); }
.va-rail span { font-size: 11px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: color .3s var(--ease); }
.va-rail .past i { background: var(--ghost); }
.va-rail .on i { background: var(--accent); }
.va-rail .on span { color: var(--ink); }
.va-cue { position: absolute; left: 50%; bottom: 28px; translate: -50% 0; display: flex; flex-direction: column; align-items: center; gap: 8px; font-size: 11.5px; color: var(--faint); pointer-events: none; }
.va-cue i { width: 1px; height: 36px; background: linear-gradient(var(--ghost), transparent); animation: va-drop 1.8s var(--ease) infinite; transform-origin: top; }
@keyframes va-drop { 0% { transform: scaleY(0); opacity: 1 } 70% { transform: scaleY(1); opacity: 1 } 100% { transform: scaleY(1); opacity: 0 } }

@media (max-width: 1180px) {
  .va-instruments { width: 290px; }
  .va-rail { left: max(32px, 5vw); right: 338px; bottom: 96px; }
}
@media (max-width: 900px) {
  .va-veil { background: linear-gradient(180deg, color-mix(in srgb, var(--ground) 92%, transparent) 0%, color-mix(in srgb, var(--ground) 40%, transparent) 34%, transparent 52%, transparent 66%, color-mix(in srgb, var(--ground) 92%, transparent) 88%); }
  .va-hero { left: 16px; right: 16px; top: 92px; translate: none; }
  .va-hero h1 { font-size: 40px; }
  .lede { font-size: 15px; margin-top: 14px; }
  .actions { margin-top: 20px; }
  .va-copy { left: 16px; right: 16px; top: 76px; translate: none; width: auto; }
  .va-copy h2 { font-size: 24px; margin-top: 10px; }
  .body { font-size: 13.5px; line-height: 1.7; margin-top: 8px; }
  .va-local { margin-top: 12px; }
  .va-instruments { left: 16px; right: 16px; top: auto; bottom: 34px; width: auto; }
  .va-gate { top: auto; bottom: 150px; }
  .va-rail { left: 16px; right: 16px; bottom: 14px; }
  .va-rail span { display: none; }
  .va-rail button { padding: 4px 0; }
  .va-cue { display: none; }
}
</style>
