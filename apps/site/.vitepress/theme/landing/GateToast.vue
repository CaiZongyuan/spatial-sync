<script setup lang="ts">
import { computed } from 'vue'
import { window01 } from './story'

const props = defineProps<{ t: number }>()
const GATES = [
  { a: 31.9, b: 33.2, press: 32.45, n: 1, title: '批准训练计划 plan-0043', meta: '3 个 seed · 预算 4 GPU·h · 附访谈记录与假设' },
  { a: 39.35, b: 40.6, press: 39.85, n: 2, title: '把策略 插管 v3 设为默认程序', meta: '评估 94/100 · 接受线 85 · 替换示教动作' },
]
const gate = computed(() => GATES.find((g) => props.t > g.a - 0.1 && props.t < g.b + 0.1))
const o = computed(() => (gate.value ? window01(props.t, gate.value.a, gate.value.b, 0.25) : 0))
const pressed = computed(() => !!gate.value && props.t >= gate.value.press)
</script>

<template>
  <div v-if="gate && o > 0.01" class="gt pl-panel" :style="{ opacity: o, transform: `translateY(${(1 - o) * 10}px) scale(${0.98 + 0.02 * o})` }" role="status">
    <div class="pl-panel-head"><span class="who"><svg viewBox="0 0 16 16"><path d="M8 1.8 13 3.6v4c0 3.1-2.1 5.4-5 6.6-2.9-1.2-5-3.5-5-6.6v-4z" /></svg>人工关口 {{ gate.n }} / 2</span><span>待审批</span></div>
    <div class="pl-panel-body">
      <p class="title">{{ gate.title }}</p>
      <p class="meta">{{ gate.meta }}</p>
      <div class="actions">
        <span class="b">驳回</span>
        <span class="b primary" :class="{ pressed }">{{ pressed ? '已批准' : '批准' }}<svg v-if="pressed" viewBox="0 0 12 12"><path d="M2.5 6.2 5 8.6l4.6-5" /></svg></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gt { width: 300px; border-color: color-mix(in srgb, var(--accent) 40%, var(--line)); }
.who { display: inline-flex; align-items: center; gap: 6px; color: var(--accent-ink); }
.who svg { width: 13px; height: 13px; fill: none; stroke: currentColor; stroke-width: 1.4; }
.title { font-size: 14px; color: var(--ink); font-weight: 500; }
.meta { margin-top: 4px; font-size: 12px; color: var(--muted); }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.b { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 12px; border-radius: 7px; border: 1px solid var(--line-2); font-size: 12.5px; color: var(--muted); }
.b.primary { background: var(--ink); border-color: var(--ink); color: var(--ground); transition: transform .15s var(--ease), background .2s var(--ease); }
.b.primary.pressed { background: var(--ok); border-color: var(--ok); color: var(--ground); transform: scale(.97); }
.b svg { width: 11px; height: 11px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; }
</style>
