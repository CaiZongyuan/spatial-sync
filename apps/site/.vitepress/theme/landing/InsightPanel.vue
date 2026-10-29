<script setup lang="ts">
// The panel that explains what the current chapter is showing. It reads the film
// clock; the lab story's panels read the lab clock.
import { computed } from 'vue'
import { chapterAt, GOAL, clamp, labT } from './story'
import WorldPanel from './WorldPanel.vue'
import Inventory from './Inventory.vue'
import PlanPanel from './PlanPanel.vue'
import AttemptGrid from './AttemptGrid.vue'
import TrainChart from './TrainChart.vue'
import ResultChart from './ResultChart.vue'
import LoopRing from './LoopRing.vue'
import Library from './Library.vue'
import ScaleStats from './ScaleStats.vue'

const props = defineProps<{ t: number }>()
const id = computed(() => chapterAt(props.t).id)
const lab = computed(() => labT(props.t))
const typed = computed(() => GOAL.text.slice(0, Math.floor(clamp((props.t - GOAL.t) / (GOAL.type ?? 1)) * GOAL.text.length)))
</script>

<template>
  <div class="ip">
    <WorldPanel v-if="id === 'build'" :t="t" />
    <div v-else-if="id === 'goal'" class="pl-panel goal">
      <div class="pl-panel-head"><span>目标 · 人唯一一次输入</span></div>
      <p class="pl-panel-body q">「{{ typed }}<i class="caret" v-if="typed.length < GOAL.text.length" />{{ typed.length >= GOAL.text.length ? '」' : '' }}</p>
    </div>
    <Inventory v-else-if="id === 'sense'" :t="lab" />
    <PlanPanel v-else-if="id === 'plan' || id === 'run'" :t="lab" />
    <AttemptGrid v-else-if="id === 'find'" :t="lab" />
    <template v-else-if="id === 'evolve'">
      <TrainChart :t="lab" />
      <Library v-if="lab > 40.4" :t="lab" />
    </template>
    <template v-else-if="id === 'result'">
      <ResultChart :t="lab" />
      <Library :t="lab" />
    </template>
    <LoopRing v-else-if="id === 'loop'" :t="lab" :size="220" />
    <ScaleStats v-else-if="id === 'scale'" :t="lab" />
  </div>
</template>

<style scoped>
.ip { display: flex; flex-direction: column; gap: 10px; }
.q { font-family: var(--font-display); font-size: 17px; line-height: 1.6; color: var(--ink); min-height: 74px; }
.caret { display: inline-block; width: 8px; height: 17px; margin-left: 2px; vertical-align: -2px; background: var(--ink-2); animation: pl-caret 1s infinite; }
</style>
