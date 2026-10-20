<script setup lang="ts">
// Loops one chapter of the film while it is on screen.
import { onBeforeUnmount, onMounted, ref } from 'vue'
import StageCanvas from './StageCanvas.vue'

const props = withDefaults(defineProps<{
  from: number
  to: number
  hold?: number
  chapter?: string
  framing?: 'film' | 'chapter'
  still?: number
  labels?: boolean
  sim?: boolean
  city?: boolean
}>(), { hold: 1.2, framing: 'chapter', labels: true, sim: false, city: false })

const t = ref(props.still ?? props.from)
const reduced = ref(false)
let visible = false
let raf = 0
let last = 0
let clock = 0

onMounted(() => {
  reduced.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (reduced.value) { t.value = props.still ?? props.to - 0.4; return }
  last = performance.now()
  const span = props.to - props.from
  const tick = (now: number) => {
    const dt = Math.min(0.1, (now - last) / 1000)
    last = now
    if (visible && !document.hidden) {
      clock = (clock + dt) % (span + props.hold)
      t.value = props.from + Math.min(clock, span)
    }
    raf = requestAnimationFrame(tick)
  }
  raf = requestAnimationFrame(tick)
})
onBeforeUnmount(() => cancelAnimationFrame(raf))
</script>

<template>
  <StageCanvas :t="t" :framing="framing" :chapter="chapter" :labels="labels" :sim="sim" :city="city" :reduced="reduced" lazy @visible="(v) => { visible = v; if (v) clock = 0 }">
    <slot :t="t" />
  </StageCanvas>
</template>
