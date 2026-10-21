<script setup lang="ts">
import { computed } from 'vue'
import { hudAt } from './story'

const props = defineProps<{ t: number }>()
const h = computed(() => hudAt(props.t))
const fmt = (n: number) => n.toLocaleString('en-US')
</script>

<template>
  <div class="hud pl-panel">
    <div class="cell"><span class="k">运行</span><span class="v mono">第 {{ h.day }} 天 <small>{{ h.clock }}</small></span></div>
    <div class="cell"><span class="k">已执行动作</span><span class="v mono">{{ fmt(h.actions) }}</span></div>
    <div class="cell human" :class="{ hit: h.interventions > 0 }"><span class="k">人工介入</span><span class="v mono">{{ h.interventions }} <small>次</small></span></div>
  </div>
</template>

<style scoped>
.hud { display: flex; align-items: stretch; padding: 10px 6px; }
.cell { display: flex; flex-direction: column; gap: 3px; padding: 0 14px; border-left: 1px solid var(--line); min-width: 0; }
.cell:first-child { border-left: 0; }
.k { font-family: var(--font-mono); font-size: 10px; letter-spacing: .06em; color: var(--faint); white-space: nowrap; }
.v { font-size: 17px; color: var(--ink); white-space: nowrap; }
.v small { font-size: 11.5px; color: var(--muted); }
.human.hit .v { color: var(--accent-ink); }
</style>
