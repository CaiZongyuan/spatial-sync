<script setup lang="ts">
import { computed } from 'vue'
import { ramp } from './story'

const props = defineProps<{ t: number }>()
const ITEMS = [
  { at: 5.9, name: '试管架', type: 'labware', state: '8 管样本' },
  { at: 6.3, name: 'FR3 机械臂', type: 'robot_arm/v1', state: '空闲' },
  { at: 6.8, name: '离心机', type: 'centrifuge/v1', state: '0 rpm' },
  { at: 7.3, name: '培养箱', type: 'incubator/v1', state: '4 温区' },
  { at: 7.8, name: '生物安全柜', type: 'bsc/v1', state: '风速正常' },
  { at: 8.4, name: '环境传感器 ×3', type: 'sensor/v1', state: '22.4 °C' },
  { at: 9.2, name: '检测仪', type: 'reader/v1', state: '缺校准记录', warn: true },
]
const rows = computed(() => ITEMS.map((i) => ({ ...i, o: ramp(props.t, i.at, i.at + 0.45) })))
</script>

<template>
  <div class="inv pl-panel">
    <div class="pl-panel-head"><span>实验室目录 · Catalog</span><span>{{ rows.filter((r) => r.o > 0.5).length }} / 7</span></div>
    <ul class="pl-panel-body">
      <li v-for="r in rows" :key="r.name" :class="{ warn: r.warn }" :style="{ opacity: 0.12 + 0.88 * r.o, transform: `translateX(${(1 - r.o) * 6}px)` }">
        <span class="n">{{ r.name }}</span><span class="ty mono">{{ r.type }}</span><span class="st">{{ r.state }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
ul { list-style: none; margin: 0; }
li { display: grid; grid-template-columns: 1fr auto; grid-template-rows: auto auto; column-gap: 10px; padding: 7px 0; border-top: 1px solid var(--line); }
li:first-child { border-top: 0; }
.n { font-size: 12.5px; color: var(--ink); }
.ty { grid-row: 2; font-size: 10.5px; color: var(--faint); }
.st { grid-row: 1 / 3; grid-column: 2; align-self: center; font-size: 12px; color: var(--muted); }
li.warn .st { color: var(--warn); }
</style>
