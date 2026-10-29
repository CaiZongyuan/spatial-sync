<script setup lang="ts">
import { computed } from 'vue'
import { BUILT, consoleAt, clamp, labT } from './story'

const props = withDefaults(defineProps<{ t: number; limit?: number }>(), { limit: 7 })
const lines = computed(() => consoleAt(props.t, props.limit))
const lab = computed(() => labT(props.t))
const working = computed(() => (props.t > 0.4 && props.t < BUILT) || (lab.value > 3.6 && lab.value < 52))
const glyph: Record<string, string> = { human: '人', agent: '◆', cmd: '$', out: '→', alert: '!', gate: '✓', good: '✓' }
</script>

<template>
  <div class="pc pl-panel">
    <div class="pl-panel-head">
      <span class="pc-who"><i :class="{ on: working }" />agent · lab-operator</span>
      <span>{{ t < BUILT ? (working ? '构建世界' : '待命') : working ? '工作中' : lab < 3.6 ? '等待目标' : '空闲' }}</span>
    </div>
    <ol class="pc-lines">
      <li v-for="l in lines" :key="l.t" :class="l.kind" :style="{ opacity: clamp(0.35 + l.age * 2.5) }">
        <span class="g">{{ glyph[l.kind] }}</span>
        <span class="x">{{ l.shown }}<i v-if="l.typing" class="caret" /></span>
      </li>
      <li v-if="!lines.length" class="idle"><span class="g">人</span><span class="x"><i class="caret" /></span></li>
    </ol>
  </div>
</template>

<style scoped>
.pc { overflow: hidden; }
.pc-who { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); }
.pc-who i { width: 7px; height: 7px; border-radius: 50%; background: var(--ghost); }
.pc-who i.on { background: var(--accent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 20%, transparent); animation: pl-pulse 1.6s infinite; }
.pc-lines { list-style: none; margin: 0; padding: 10px 14px 14px; display: flex; flex-direction: column; gap: 7px; min-height: 0; }
.pc-lines li { display: grid; grid-template-columns: 18px 1fr; gap: 8px; font-size: 12.5px; line-height: 1.5; color: var(--ink-2); }
.g { font-family: var(--font-mono); font-size: 11px; color: var(--faint); text-align: center; line-height: 18.75px; }
.x { min-width: 0; overflow-wrap: anywhere; }
li.human .g { color: var(--ground); background: var(--ink-2); border-radius: 4px; font-size: 10px; height: 18px; line-height: 18px; }
li.human .x { color: var(--ink); font-weight: 500; }
li.agent .g { color: var(--accent); }
li.agent .x { color: var(--ink-2); }
li.cmd .x { font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-2); }
li.out .x { font-family: var(--font-mono); font-size: 11.5px; color: var(--muted); }
li.alert .g, li.alert .x { color: var(--warn); }
li.gate .g { color: var(--on-accent); background: var(--accent); border-radius: 4px; height: 18px; line-height: 18px; }
li.gate .x { color: var(--accent-ink); }
li.good .g, li.good .x { color: var(--ok); }
.caret { display: inline-block; width: 7px; height: 14px; margin-left: 2px; vertical-align: -2px; background: var(--ink-2); animation: pl-caret 1s infinite; }
</style>
