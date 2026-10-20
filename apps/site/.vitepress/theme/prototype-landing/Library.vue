<script setup lang="ts">
import { computed } from 'vue'
import { libraryAt, ramp } from './story'

const props = defineProps<{ t: number }>()
const lib = computed(() => libraryAt(props.t))
const fmt = (n: number) => n.toLocaleString('en-US')
</script>

<template>
  <div class="lib pl-panel">
    <div class="pl-panel-head"><span>能力库</span><span class="mono count">{{ fmt(lib.count) }} 项</span></div>
    <ul class="pl-panel-body">
      <li v-for="c in lib.cards" :key="c.title" :class="c.kind" :style="{ opacity: ramp(t, c.at, c.at + 0.35), transform: `translateY(${(1 - ramp(t, c.at, c.at + 0.35)) * 8}px)` }">
        <span class="badge mono">{{ c.kind === 'policy' ? '策略' : '技能' }}</span>
        <span class="tt">{{ c.title }}</span>
        <span class="meta">{{ c.meta }}</span>
      </li>
      <li v-if="!lib.cards.length" class="empty">训练或总结出的能力会出现在这里</li>
    </ul>
  </div>
</template>

<style scoped>
ul { list-style: none; margin: 0; display: flex; flex-direction: column; gap: 6px; }
.count { color: var(--ink-2); }
li { display: grid; grid-template-columns: auto 1fr; column-gap: 10px; align-items: center; padding: 8px 10px; border-radius: 9px; border: 1px solid var(--line); background: var(--surface-2); }
.badge { grid-row: 1 / 3; height: 22px; padding: 0 7px; display: inline-flex; align-items: center; border-radius: 5px; font-size: 10.5px; }
.policy .badge { color: var(--on-accent); background: var(--accent); }
.skill .badge { color: var(--accent-ink); border: 1px solid color-mix(in srgb, var(--accent) 50%, var(--line-2)); }
.tt { font-size: 13px; color: var(--ink); }
.meta { font-size: 11px; color: var(--muted); }
.empty { display: block; border-style: dashed; background: transparent; font-size: 12px; color: var(--faint); }
</style>
