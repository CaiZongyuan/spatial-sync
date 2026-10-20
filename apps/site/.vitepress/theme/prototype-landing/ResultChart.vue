<script setup lang="ts">
import { computed } from 'vue'
import { TEMPS, growth, resultProgress } from './story'

const props = withDefaults(defineProps<{ t: number; bare?: boolean }>(), { bare: false })
const W = 300, H = 140, P = { l: 30, r: 54, t: 10, b: 22 }
const MAXH = 72, MAXV = 1.6
const x = (h: number) => P.l + (h / MAXH) * (W - P.l - P.r)
const y = (v: number) => P.t + (1 - v / MAXV) * (H - P.t - P.b)
const prog = computed(() => resultProgress(props.t))
const series = computed(() => {
  const hEnd = prog.value * MAXH
  return TEMPS.map((temp) => {
    const pts: string[] = []
    const n = Math.max(2, Math.ceil(prog.value * 48))
    for (let i = 0; i <= n; i++) {
      const h = (i / n) * hEnd
      pts.push(`${i ? 'L' : 'M'}${x(h).toFixed(1)},${y(growth(temp, h)).toFixed(1)}`)
    }
    return { temp, d: pts.join(''), ex: x(hEnd), ey: y(growth(temp, hEnd)), best: temp === 37 }
  })
})
</script>

<template>
  <div class="rc" :class="{ 'pl-panel': !bare }">
    <div class="pl-panel-head"><span>样本生长 · OD600 · 72 h</span><span class="pl-demo">示意</span></div>
    <div class="pl-panel-body">
      <svg :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="四个温度的生长曲线，37 °C 组最高">
        <g class="grid">
          <line v-for="v in [0, 0.8, 1.6]" :key="v" :x1="P.l" :x2="W - P.r" :y1="y(v)" :y2="y(v)" />
          <text v-for="v in [0, 0.8, 1.6]" :key="'t' + v" :x="P.l - 6" :y="y(v) + 3.5" text-anchor="end">{{ v }}</text>
          <text :x="P.l" :y="H - 6">0 h</text>
          <text :x="W - P.r" :y="H - 6" text-anchor="end">72 h</text>
        </g>
        <g v-for="s in series" :key="s.temp" :class="{ best: s.best }">
          <path :d="s.d" />
          <circle v-if="prog > 0.02" :cx="s.ex" :cy="s.ey" :r="s.best ? 4 : 3" />
          <text v-if="prog > 0.6" :x="s.ex + 8" :y="s.ey + 4">{{ s.temp }} °C</text>
        </g>
      </svg>
    </div>
  </div>
</template>

<style scoped>
svg { width: 100%; height: auto; display: block; overflow: visible; }
.grid line { stroke: var(--line); }
.grid text { font-family: var(--font-mono); font-size: 9.5px; fill: var(--faint); }
g path { fill: none; stroke: var(--ghost); stroke-width: 1.6; stroke-linecap: round; }
g circle { fill: var(--ghost); stroke: var(--surface); stroke-width: 2; }
g text { font-family: var(--font-mono); font-size: 10.5px; fill: var(--muted); }
g.best path { stroke: var(--accent); stroke-width: 2.4; }
g.best circle { fill: var(--accent); }
g.best text { fill: var(--ink); font-weight: 500; }
</style>
