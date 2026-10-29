<script setup lang="ts">
import { computed } from 'vue'
import { scaleAt } from './story'

const props = defineProps<{ t: number }>()
const s = computed(() => scaleAt(props.t))
const fmt = (n: number) => n.toLocaleString('en-US')
</script>

<template>
  <div class="ss pl-panel">
    <div class="pl-panel-head"><span>所有工作站</span></div>
    <div class="pl-panel-body grid">
      <div><span class="v">{{ fmt(s.labs) }}</span><span class="k">个接入的工作站</span></div>
      <div><span class="v">{{ fmt(s.goals) }}</span><span class="k">个进行中的任务</span></div>
      <div><span class="v accent">{{ fmt(s.skills) }}</span><span class="k">项经过验证、可复用的技能</span></div>
    </div>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr; gap: 12px; }
.grid > div { display: flex; flex-direction: column; gap: 2px; }
.v { font-family: var(--font-display); font-size: 30px; line-height: 1.05; color: var(--ink); font-variant-numeric: tabular-nums; }
.v.accent { color: var(--accent-ink); }
.k { font-size: 12px; color: var(--muted); }
</style>
