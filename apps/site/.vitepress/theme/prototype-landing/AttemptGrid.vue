<script setup lang="ts">
import { computed } from 'vue'
import { ATTEMPTS, clamp } from './story'

const props = defineProps<{ t: number }>()
// Attempts reveal one by one as the agent replays its recordings.
const shown = computed(() => Math.floor(clamp((props.t - 26.6) / 1.6) * ATTEMPTS.length))
const rate = Math.floor((ATTEMPTS.filter(Boolean).length / ATTEMPTS.length) * 100)
</script>

<template>
  <div class="ag pl-panel">
    <div class="pl-panel-head"><span>插入离心机 · 近 40 次</span><span class="pl-demo">示意</span></div>
    <div class="pl-panel-body">
      <div class="big"><span class="v">{{ rate }}</span><span class="u">%</span><span class="cap">成功率</span></div>
      <div class="dots" role="img" :aria-label="`近 40 次插入中成功 25 次`">
        <i v-for="(ok, i) in ATTEMPTS" :key="i" :class="[ok ? 'ok' : 'bad', { off: i >= shown }]" />
      </div>
      <div class="legend"><span><i class="ok" />成功</span><span><i class="bad" />失败 · 集中在对位阶段</span></div>
    </div>
  </div>
</template>

<style scoped>
.big { display: flex; align-items: baseline; gap: 4px; }
.v { font-family: var(--font-display); font-size: 40px; line-height: 1; color: var(--ink); font-variant-numeric: tabular-nums; }
.u { font-size: 16px; color: var(--muted); }
.cap { margin-left: 8px; font-size: 12px; color: var(--muted); }
.dots { margin-top: 12px; display: grid; grid-template-columns: repeat(10, 12px); gap: 6px; }
.dots i, .legend i { width: 12px; height: 12px; border-radius: 50%; display: block; transition: opacity .25s var(--ease), transform .25s var(--ease); }
.ok { background: var(--ok); }
.bad { background: transparent; box-shadow: inset 0 0 0 2px var(--bad); position: relative; }
.bad::after { content: ''; position: absolute; inset: 5px; border-radius: 50%; background: var(--bad); }
.off { opacity: .14; transform: scale(.7); }
.legend { margin-top: 12px; display: flex; gap: 14px; font-size: 11.5px; color: var(--muted); }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 9px; height: 9px; }
.legend .bad::after { inset: 3px; }
</style>
