<script setup lang="ts">
import { computed } from 'vue'
import { scaleAt } from './story'

const props = withDefaults(defineProps<{ t: number; bare?: boolean }>(), { bare: false })
const s = computed(() => scaleAt(props.t))
const fmt = (n: number) => n.toLocaleString('en-US')
</script>

<template>
  <div class="ss" :class="{ 'pl-panel': !bare, bare }">
    <div class="pl-panel-head"><span>所有实验室</span><span class="pl-demo">示意</span></div>
    <div class="pl-panel-body grid">
      <div><span class="v">{{ fmt(s.labs) }}</span><span class="k">座实验室</span></div>
      <div><span class="v">{{ fmt(s.goals) }}</span><span class="k">个进行中的目标</span></div>
      <div><span class="v accent">{{ fmt(s.skills) }}</span><span class="k">项共享的策略与技能</span></div>
    </div>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr; gap: 12px; }
.grid > div { display: flex; flex-direction: column; gap: 2px; }
.v { font-family: var(--font-display); font-size: 30px; line-height: 1.05; color: var(--ink); font-variant-numeric: tabular-nums; }
.v.accent { color: var(--accent-ink); }
.k { font-size: 12px; color: var(--muted); }
.ss.bare .pl-panel-head, .ss.bare .pl-panel-body { padding-left: 0; padding-right: 0; }
.ss.bare .grid { grid-template-columns: repeat(3, auto); justify-content: start; column-gap: 48px; }
@media (max-width: 640px) { .ss.bare .grid { grid-template-columns: 1fr; } }
</style>
