<script setup lang="ts">
// PROTOTYPE (#87). Floating variant switcher; development builds only.
import { onBeforeUnmount, onMounted } from 'vue'

const props = defineProps<{ variants: { key: string; name: string }[]; current: string }>()
const emit = defineEmits<{ change: [string] }>()
const dev = import.meta.env.DEV

function step(d: number) {
  const i = props.variants.findIndex((v) => v.key === props.current)
  emit('change', props.variants[(i + d + props.variants.length) % props.variants.length].key)
}

function onKey(e: KeyboardEvent) {
  const el = document.activeElement as HTMLElement | null
  if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
  if (e.key === 'ArrowLeft') step(-1)
  if (e.key === 'ArrowRight') step(1)
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div v-if="dev" class="psw" role="toolbar" aria-label="原型切换">
    <button aria-label="上一个方案" @click="step(-1)">←</button>
    <span><b>{{ current }}</b> {{ variants.find((v) => v.key === current)?.name }}</span>
    <button aria-label="下一个方案" @click="step(1)">→</button>
  </div>
</template>

<style scoped>
.psw { position: fixed; left: 50%; bottom: 18px; translate: -50% 0; z-index: 200; display: flex; align-items: center; gap: 4px; padding: 4px; border-radius: 999px; background: #ff5a1f; color: #1a0a02; font: 500 12.5px/1 ui-sans-serif, system-ui, sans-serif; box-shadow: 0 10px 30px -8px rgba(0, 0, 0, .5); }
.psw button { width: 30px; height: 30px; border-radius: 50%; border: 0; background: rgba(0, 0, 0, .14); color: inherit; cursor: pointer; font-size: 14px; }
.psw button:hover { background: rgba(0, 0, 0, .24); }
.psw span { padding: 0 10px; white-space: nowrap; }
</style>
