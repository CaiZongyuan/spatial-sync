<script setup lang="ts">
// The panel that explains what the current chapter is showing.
import { computed } from 'vue'
import { chapterAt, CONSOLE, clamp } from './story'
import Inventory from './Inventory.vue'
import PlanPanel from './PlanPanel.vue'
import AttemptGrid from './AttemptGrid.vue'
import TrainChart from './TrainChart.vue'
import ResultChart from './ResultChart.vue'
import LoopRing from './LoopRing.vue'
import Library from './Library.vue'
import ScaleStats from './ScaleStats.vue'

const props = defineProps<{ t: number; chapter?: string }>()
const id = computed(() => props.chapter ?? chapterAt(props.t).id)
const goal = CONSOLE[0]
const typed = computed(() => goal.text.slice(0, Math.floor(clamp((props.t - goal.t) / (goal.type ?? 1)) * goal.text.length)))
</script>

<template>
  <div class="ip">
    <div v-if="id === 'goal'" class="pl-panel goal">
      <div class="pl-panel-head"><span>目标 · 人唯一一次输入</span></div>
      <p class="pl-panel-body q">「{{ typed }}<i class="caret" v-if="typed.length < goal.text.length" />{{ typed.length >= goal.text.length ? '」' : '' }}</p>
    </div>
    <Inventory v-else-if="id === 'sense'" :t="t" />
    <PlanPanel v-else-if="id === 'plan' || id === 'run'" :t="t" />
    <AttemptGrid v-else-if="id === 'find'" :t="t" />
    <template v-else-if="id === 'evolve'">
      <TrainChart :t="t" />
      <Library v-if="t > 40.4" :t="t" />
    </template>
    <template v-else-if="id === 'result'">
      <ResultChart :t="t" />
      <Library :t="t" />
    </template>
    <LoopRing v-else-if="id === 'loop'" :t="t" :size="220" />
    <ScaleStats v-else-if="id === 'scale'" :t="t" />
  </div>
</template>

<style scoped>
.ip { display: flex; flex-direction: column; gap: 10px; }
.q { font-family: var(--font-display); font-size: 17px; line-height: 1.6; color: var(--ink); min-height: 74px; }
.caret { display: inline-block; width: 8px; height: 17px; margin-left: 2px; vertical-align: -2px; background: var(--ink-2); animation: pl-caret 1s infinite; }
</style>
