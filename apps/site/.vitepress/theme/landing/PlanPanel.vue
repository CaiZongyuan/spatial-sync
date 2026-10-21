<script setup lang="ts">
import { computed } from 'vue'
import { planAt, planRevision } from './story'

const props = defineProps<{ t: number }>()
const steps = computed(() => planAt(props.t))
</script>

<template>
  <div class="pp pl-panel">
    <div class="pl-panel-head"><span>操作计划 · plan-0042</span><span class="rev">{{ planRevision(t) }}</span></div>
    <ol class="pl-panel-body">
      <li v-for="s in steps" :key="s.i" :class="[s.state, { hidden: !s.visible }]">
        <span class="mark"><svg v-if="s.state === 'done'" viewBox="0 0 12 12"><path d="M2.5 6.2 5 8.6l4.6-5" /></svg><b v-else-if="s.state === 'issue'">!</b></span>
        <span class="n mono">{{ String(s.i).padStart(2, '0') }}</span>
        <span class="tx">{{ s.text }}</span>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.rev { color: var(--accent); }
ol { list-style: none; margin: 0; display: flex; flex-direction: column; gap: 2px; }
li { display: grid; grid-template-columns: 18px 22px 1fr; align-items: center; gap: 6px; padding: 6px 0; font-size: 12.5px; color: var(--muted); border-top: 1px solid var(--line); transition: opacity .35s var(--ease), transform .35s var(--ease); }
li:first-child { border-top: 0; }
li.hidden { opacity: 0; transform: translateY(4px); }
.n { font-size: 10.5px; color: var(--faint); }
.mark { width: 14px; height: 14px; border-radius: 50%; border: 1px solid var(--ghost); display: grid; place-items: center; }
.mark svg { width: 10px; height: 10px; fill: none; stroke: var(--ground); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
li.active { color: var(--ink); }
li.active .mark { border-color: var(--accent); box-shadow: inset 0 0 0 3px var(--surface), inset 0 0 0 7px var(--accent); animation: pl-pulse 1.4s infinite; }
li.done { color: var(--ink-2); }
li.done .mark { background: var(--ok); border-color: var(--ok); }
li.issue { color: var(--bad); }
li.issue .mark { border-color: var(--bad); }
li.issue .mark b { font-size: 9px; font-family: var(--font-mono); color: var(--bad); }
</style>
