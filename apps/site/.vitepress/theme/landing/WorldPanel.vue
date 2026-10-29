<script setup lang="ts">
// World Building, layer by layer: what the agent has made computable so far.
import { computed } from 'vue'

const props = defineProps<{ t: number }>()
const LAYERS = [
  { name: '外观', detail: '参考照片 · 素材库模型', at: 1.7 },
  { name: '几何', detail: '尺寸 · 坐标 · 布局', at: 3.1 },
  { name: '物理', detail: '碰撞体 · 关节 · 限位', at: 4.9 },
  { name: '设备语义', detail: '能做什么 · 何时不能做', at: 6.7 },
]
const layers = computed(() => LAYERS.map((l, i) => {
  const done = props.t >= l.at
  const active = !done && props.t > 0.6 && (i === 0 || props.t >= LAYERS[i - 1].at)
  return { ...l, state: done ? 'done' : active ? 'active' : 'pending' }
}))
const ok = computed(() => props.t >= 8.1)
</script>

<template>
  <div class="wp pl-panel">
    <div class="pl-panel-head"><span>World Building</span><span :class="{ ok }">{{ ok ? 'lab-culture@1' : '构建中' }}</span></div>
    <ol class="pl-panel-body">
      <li v-for="(l, i) in layers" :key="l.name" :class="l.state">
        <span class="mark"><svg v-if="l.state === 'done'" viewBox="0 0 12 12"><path d="M2.5 6.2 5 8.6l4.6-5" /></svg></span>
        <span class="n mono">{{ String(i + 1).padStart(2, '0') }}</span>
        <span class="tx"><b>{{ l.name }}</b>{{ l.detail }}</span>
      </li>
    </ol>
    <p class="verdict" :class="{ ok }">{{ ok ? '验证通过：可以仿真，也可以操作' : '看起来对还不够，物理上也要对' }}</p>
  </div>
</template>

<style scoped>
.pl-panel-head .ok { color: var(--ok); }
ol { list-style: none; margin: 0; display: flex; flex-direction: column; gap: 2px; }
li { display: grid; grid-template-columns: 18px 22px 1fr; align-items: center; gap: 6px; padding: 7px 0; font-size: 12.5px; color: var(--muted); border-top: 1px solid var(--line); }
li:first-child { border-top: 0; }
.n { font-size: 10.5px; color: var(--faint); }
.tx { display: flex; align-items: baseline; gap: 10px; }
.tx b { font-weight: 500; color: var(--ink-2); min-width: 4em; }
.mark { width: 14px; height: 14px; border-radius: 50%; border: 1px solid var(--ghost); display: grid; place-items: center; }
.mark svg { width: 10px; height: 10px; fill: none; stroke: var(--ground); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
li.active { color: var(--ink); }
li.active .mark { border-color: var(--accent); box-shadow: inset 0 0 0 3px var(--surface), inset 0 0 0 7px var(--accent); animation: pl-pulse 1.4s infinite; }
li.done .mark { background: var(--accent); border-color: var(--accent); }
.verdict { margin: 0; padding: 0 14px 14px; font-size: 12px; color: var(--faint); }
.verdict.ok { color: var(--ok); }
</style>
