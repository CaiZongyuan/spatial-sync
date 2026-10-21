<script setup lang="ts">
// Mounts one LabStage and renders it from the `t` prop while it is on screen.
import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import type { LabStage } from './stage'
import { labelsAt, ramp, type LabelSpec } from './story'

const props = withDefaults(defineProps<{
  t: number
  offsetX?: number
  offsetY?: number
  reduced?: boolean
}>(), { offsetX: 0, offsetY: 0, reduced: false })

const emit = defineEmits<{ ready: [] }>()

const host = ref<HTMLDivElement>()
const canvas = ref<HTMLCanvasElement>()
const items = shallowRef<(LabelSpec & { x: number; y: number })[]>([])
const mask = ref('')
let stage: LabStage | null = null
let raf = 0
let visible = false
let disposed = false
let ro: ResizeObserver | null = null
let io: IntersectionObserver | null = null
let mo: MutationObserver | null = null

async function create() {
  // three.js stays out of the entry chunk.
  const { LabStage } = await import('./stage')
  if (disposed || !canvas.value || !host.value) return
  stage = new LabStage(canvas.value, { reducedMotion: props.reduced })
  const r = host.value.getBoundingClientRect()
  stage.resize(r.width, r.height)
  frame()
  emit('ready')
  loop()
}

function frame() {
  if (!stage) return
  stage.setOffset(props.offsetX, props.offsetY)
  stage.update(props.t, performance.now() / 1000)
  stage.render()
  // The scale chapter dissolves the frame edge so the city reads as endless.
  const m = ramp(props.t, 52.6, 56)
  mask.value = m > 0.001 ? `radial-gradient(ellipse 72% 70% at ${50 + props.offsetX * 100}% ${50 - props.offsetY * 100}%, #000 ${140 - 96 * m}%, transparent ${160 - 62 * m}%)` : ''
  const out: (LabelSpec & { x: number; y: number })[] = []
  const narrow = (host.value?.clientWidth ?? 1000) < 600
  for (const l of labelsAt(props.t)) {
    if (narrow && l.tone === 'plain' && !['arm', 'cf', 'inc'].includes(l.id)) continue
    const p = stage.project(l.anchor)
    if (p.visible) out.push({ ...l, x: p.x, y: p.y })
  }
  items.value = out
}

function loop() {
  const tick = () => {
    if (visible && !document.hidden) frame()
    raf = requestAnimationFrame(tick)
  }
  raf = requestAnimationFrame(tick)
}

/** Fixed-size images of the lab at each time, for the chapter index. */
function stills(times: number[], w: number, h: number): string[] {
  if (!stage) return []
  const out = times.map((t) => stage!.still(t, w, h))
  // Resizing cleared the live canvas; repaint it before the browser composites.
  frame()
  return out
}

onMounted(() => {
  ro = new ResizeObserver(([e]) => {
    stage?.resize(e.contentRect.width, e.contentRect.height)
    frame()
  })
  ro.observe(host.value!)
  io = new IntersectionObserver(([e]) => { visible = e.isIntersecting })
  io.observe(host.value!)
  mo = new MutationObserver(() => { stage?.applyTheme(); frame() })
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  create()
})

onBeforeUnmount(() => {
  disposed = true
  cancelAnimationFrame(raf)
  ro?.disconnect(); io?.disconnect(); mo?.disconnect()
  stage?.dispose()
  stage = null
})

defineExpose({ stills })
</script>

<template>
  <div ref="host" class="pl-stage">
    <canvas ref="canvas" class="pl-stage-canvas" :style="mask ? { maskImage: mask, WebkitMaskImage: mask } : undefined" />
    <div class="pl-labels" aria-hidden="true">
      <div v-for="l in items" :key="l.id" class="pl-label" :class="l.tone" :style="{ transform: `translate(${l.x}px, ${l.y}px) translate(-50%, -100%)`, opacity: l.o }">
        <span>{{ l.text }}</span><b /><em />
      </div>
    </div>
  </div>
</template>

<style scoped>
.pl-stage { position: relative; }
.pl-stage-canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
</style>
