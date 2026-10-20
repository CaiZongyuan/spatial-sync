<script setup lang="ts">
// PROTOTYPE (#87). Mounts one LabStage and renders it from the `t` prop.
import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import type { LabStage, StageOptions } from './stage'
import { labelsAt, ramp, type LabelSpec } from './story'

const props = withDefaults(defineProps<{
  t: number
  framing?: 'film' | 'chapter'
  chapter?: string
  offsetX?: number
  offsetY?: number
  zoom?: number
  labels?: boolean
  lazy?: boolean
  sim?: boolean
  city?: boolean
  reduced?: boolean
}>(), { framing: 'film', offsetX: 0, offsetY: 0, zoom: 1, labels: true, lazy: false, sim: true, city: true, reduced: false })

const emit = defineEmits<{ ready: [LabStage]; visible: [boolean] }>()

const host = ref<HTMLDivElement>()
const canvas = ref<HTMLCanvasElement>()
const items = shallowRef<(LabelSpec & { x: number; y: number })[]>([])
const mask = ref('')
let stage: LabStage | null = null
let raf = 0
let visible = false
let ro: ResizeObserver | null = null
let io: IntersectionObserver | null = null
let mo: MutationObserver | null = null
let creating = false

async function create() {
  if (stage || creating || !canvas.value) return
  creating = true
  const { LabStage } = await import('./stage')
  const opts: StageOptions = { framing: props.framing, chapter: props.chapter, reducedMotion: props.reduced, sim: props.sim, city: props.city }
  stage = new LabStage(canvas.value, opts)
  const r = host.value!.getBoundingClientRect()
  stage.resize(r.width, r.height)
  creating = false
  emit('ready', stage)
  loop()
}

function frame() {
  if (!stage) return
  stage.setOffset(props.offsetX, props.offsetY)
  stage.zoom = props.zoom
  stage.update(props.t, performance.now() / 1000)
  stage.render()
  const m = ramp(props.t, 52.6, 56)
  mask.value = m > 0.001 ? `radial-gradient(ellipse 72% 70% at ${50 + props.offsetX * 100}% ${50 - props.offsetY * 100}%, #000 ${140 - 96 * m}%, transparent ${160 - 62 * m}%)` : ''
  if (props.labels) {
    const out: (LabelSpec & { x: number; y: number })[] = []
    const narrow = (host.value?.clientWidth ?? 1000) < 600
    for (const l of labelsAt(props.t)) {
      if (narrow && l.tone === 'plain' && !['arm', 'cf', 'inc'].includes(l.id)) continue
      const p = stage.project(l.anchor)
      if (p.visible) out.push({ ...l, x: p.x, y: p.y })
    }
    items.value = out
  }
}

function loop() {
  cancelAnimationFrame(raf)
  const tick = () => {
    if (visible && !document.hidden) frame()
    raf = requestAnimationFrame(tick)
  }
  raf = requestAnimationFrame(tick)
}

onMounted(() => {
  ro = new ResizeObserver(([e]) => {
    stage?.resize(e.contentRect.width, e.contentRect.height)
    if (stage && !visible) frame()
  })
  ro.observe(host.value!)
  io = new IntersectionObserver(([e]) => {
    visible = e.isIntersecting
    emit('visible', visible)
    if (visible) create()
  }, { rootMargin: props.lazy ? '60% 0px' : '0px' })
  io.observe(host.value!)
  mo = new MutationObserver(() => { stage?.applyTheme(); if (stage) frame() })
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  if (!props.lazy) create()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  ro?.disconnect(); io?.disconnect(); mo?.disconnect()
  stage?.dispose()
  stage = null
})

defineExpose({ frame, getStage: () => stage })
</script>

<template>
  <div ref="host" class="pl-stage">
    <canvas ref="canvas" class="pl-stage-canvas" :style="mask ? { maskImage: mask, WebkitMaskImage: mask } : undefined" />
    <div v-if="labels" class="pl-labels" aria-hidden="true">
      <div v-for="l in items" :key="l.id" class="pl-label" :class="l.tone" :style="{ transform: `translate(${l.x}px, ${l.y}px) translate(-50%, -100%)`, opacity: l.o }">
        <span>{{ l.text }}</span><b /><em />
      </div>
    </div>
    <slot />
  </div>
</template>

<style scoped>
.pl-stage { position: relative; }
.pl-stage-canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; border-radius: inherit; }
.pl-stage :deep(.pl-labels) { border-radius: inherit; }
</style>
