<script setup lang="ts">
import { computed } from 'vue'
import { LOOP_NODES, ramp } from './story'

const props = withDefaults(defineProps<{ t: number; size?: number }>(), { size: 240 })
const R = 84, C = 120
const prog = computed(() => ramp(props.t, 48.2, 51.0))
const circ = 2 * Math.PI * R
const nodes = LOOP_NODES.map((label, i) => {
  const a = -Math.PI / 2 + (i / LOOP_NODES.length) * Math.PI * 2
  return { label, x: C + R * Math.cos(a), y: C + R * Math.sin(a), lx: C + (R + 22) * Math.cos(a), ly: C + (R + 22) * Math.sin(a), at: i / LOOP_NODES.length }
})
const gate = nodes[4]
</script>

<template>
  <div class="lr pl-panel">
    <div class="pl-panel-head"><span>RSI 闭环</span><span>两处人工关口</span></div>
    <svg :viewBox="`0 0 ${C * 2} ${C * 2}`" :style="{ maxWidth: size + 'px' }" role="img" aria-label="RSI 闭环：构建、规划、执行、发现、进化，再回到构建">
      <circle class="track" :cx="C" :cy="C" :r="R" />
      <circle class="arc" :cx="C" :cy="C" :r="R" :stroke-dasharray="`${circ * prog} ${circ}`" :transform="`rotate(-90 ${C} ${C})`" />
      <g v-for="n in nodes" :key="n.label" :class="{ on: prog >= n.at - 0.001 }">
        <circle class="node" :cx="n.x" :cy="n.y" r="5" />
        <text :x="n.lx" :y="n.ly + 4" text-anchor="middle">{{ n.label }}</text>
      </g>
      <g class="gate" :style="{ opacity: prog > 0.66 ? 1 : 0 }">
        <rect :x="gate.x + 10" :y="gate.y - 22" width="22" height="16" rx="4" />
        <text :x="gate.x + 21" :y="gate.y - 10.5" text-anchor="middle">人</text>
        <rect :x="gate.x + 10" :y="gate.y + 6" width="22" height="16" rx="4" />
        <text :x="gate.x + 21" :y="gate.y + 17.5" text-anchor="middle">人</text>
      </g>
      <text class="core" :x="C" :y="C - 2" text-anchor="middle">RSI</text>
      <text class="core-sub" :x="C" :y="C + 16" text-anchor="middle">第 {{ prog >= 0.999 ? 2 : 1 }} 轮</text>
    </svg>
  </div>
</template>

<style scoped>
.lr { display: flex; flex-direction: column; align-items: center; }
.lr svg { padding: 6px 12px 14px; }
svg { width: 100%; height: auto; display: block; overflow: visible; }
.track { fill: none; stroke: var(--line-2); stroke-width: 1.5; }
.arc { fill: none; stroke: var(--accent); stroke-width: 2.4; stroke-linecap: round; }
.node { fill: var(--surface); stroke: var(--ghost); stroke-width: 1.5; transition: fill .3s var(--ease), stroke .3s var(--ease); }
g.on .node { fill: var(--accent); stroke: var(--accent); }
g text { font-size: 12px; fill: var(--muted); font-family: var(--font-ui); }
g.on text { fill: var(--ink); }
.gate { transition: opacity .3s var(--ease); }
.gate rect { fill: var(--surface-2); stroke: var(--line-2); }
.gate text { font-size: 10px; fill: var(--ink-2); }
.core { font-family: var(--font-display); font-size: 26px; fill: var(--ink); font-weight: 600; }
.core-sub { font-family: var(--font-mono); font-size: 10.5px; fill: var(--faint); }
</style>
