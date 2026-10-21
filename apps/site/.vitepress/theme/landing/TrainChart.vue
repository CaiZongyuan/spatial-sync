<script setup lang="ts">
import { computed } from 'vue'
import { clamp, seedCurve, trainProgress, ramp } from './story'

const props = defineProps<{ t: number }>()
const W = 300, H = 132, P = { l: 30, r: 44, t: 10, b: 22 }
const x = (s: number) => P.l + s * (W - P.l - P.r)
const y = (v: number) => P.t + (1 - v) * (H - P.t - P.b)

const prog = computed(() => trainProgress(props.t))
const N = 80
const paths = computed(() => {
  const n = Math.max(2, Math.floor(prog.value * N))
  const lo: string[] = [], hi: string[] = [], mid: string[] = []
  for (let i = 0; i <= n; i++) {
    const s = (i / N)
    const v = [0, 1, 2].map((k) => seedCurve(k, s))
    const m = (v[0] + v[1] + v[2]) / 3
    mid.push(`${i ? 'L' : 'M'}${x(s).toFixed(1)},${y(m).toFixed(1)}`)
    hi.push(`${x(s).toFixed(1)},${y(Math.max(...v)).toFixed(1)}`)
    lo.unshift(`${x(s).toFixed(1)},${y(Math.min(...v)).toFixed(1)}`)
  }
  const last = n / N
  const lastV = [0, 1, 2].map((k) => seedCurve(k, last)).reduce((a, b) => a + b) / 3
  return { mid: mid.join(''), band: `M${hi.join('L')}L${lo.join('L')}Z`, end: { x: x(last), y: y(lastV), v: Math.round(lastV * 100) } }
})
const evalO = computed(() => ramp(props.t, 38.9, 39.4))
const accept = y(0.85)
</script>

<template>
  <div class="tc pl-panel">
    <div class="pl-panel-head"><span>训练 · 插管策略 · 3 个 seed</span><span class="pl-demo">示意</span></div>
    <div class="pl-panel-body">
      <svg :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="训练成功率随步数上升，三个 seed 的均值从约 15% 升到 95%">
        <g class="grid">
          <line v-for="v in [0, 0.5, 1]" :key="v" :x1="P.l" :x2="W - P.r" :y1="y(v)" :y2="y(v)" />
          <text v-for="v in [0, 0.5, 1]" :key="'t' + v" :x="P.l - 6" :y="y(v) + 3.5" text-anchor="end">{{ v * 100 }}</text>
        </g>
        <line class="accept" :x1="P.l" :x2="W - P.r" :y1="accept" :y2="accept" />
        <text class="accept-t" :x="W - P.r + 4" :y="accept + 3.5">接受线</text>
        <path class="band" :d="paths.band" />
        <path class="mid" :d="paths.mid" />
        <circle class="endpt" :cx="paths.end.x" :cy="paths.end.y" r="4" />
        <text class="end-t" :x="paths.end.x + 8" :y="paths.end.y + 4">{{ paths.end.v }}%</text>
        <text class="axis" :x="P.l" :y="H - 6">训练步数 →</text>
      </svg>
      <div class="eval" :style="{ opacity: evalO, transform: `translateY(${(1 - evalO) * 6}px)` }">
        <span class="k">评估 · insert-100</span>
        <span class="bar"><i :style="{ width: `${94 * evalO}%` }" /><b style="left: 85%" /></span>
        <span class="v mono">94 / 100</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
svg { width: 100%; height: auto; display: block; overflow: visible; }
.grid line { stroke: var(--line); stroke-width: 1; }
.grid text, .axis, .accept-t { font-family: var(--font-mono); font-size: 9.5px; fill: var(--faint); }
.accept { stroke: var(--ghost); stroke-width: 1; stroke-dasharray: 3 3; }
.band { fill: color-mix(in srgb, var(--accent) 16%, transparent); }
.mid { fill: none; stroke: var(--accent); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.endpt { fill: var(--accent); stroke: var(--surface); stroke-width: 2; }
.end-t { font-family: var(--font-mono); font-size: 11px; fill: var(--ink); }
.eval { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); }
.k { font-family: var(--font-mono); font-size: 10.5px; color: var(--faint); }
.bar { position: relative; height: 6px; border-radius: 3px; background: var(--ground-2); }
.bar i { position: absolute; inset: 0 auto 0 0; border-radius: 3px; background: var(--ok); }
.bar b { position: absolute; top: -3px; bottom: -3px; width: 1px; background: var(--ink-2); }
.v { font-size: 13px; color: var(--ink); }
</style>
