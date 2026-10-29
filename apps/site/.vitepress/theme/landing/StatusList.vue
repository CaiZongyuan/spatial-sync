<script setup lang="ts">
// What already runs and what is next, one line each, under a part of Lab World.
import StatusChip from './StatusChip.vue'
import type { Status } from './story'

defineProps<{ items: { s: Status; t: string }[] }>()
</script>

<template>
  <ul class="sl">
    <li v-for="i in items" :key="i.t"><StatusChip :status="i.s" /><span>{{ i.t }}</span></li>
  </ul>
</template>

<style scoped>
.sl { list-style: none; margin: 0; padding: 16px 0 0; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 10px; }
.sl li { display: grid; grid-template-columns: 64px 1fr; align-items: baseline; gap: 12px; font-size: 13.5px; line-height: 1.6; color: var(--ink-2); }
.sl :deep(.pl-chip) { justify-self: start; }
@media (max-width: 620px) { .sl li { grid-template-columns: 1fr; gap: 4px; } }
</style>
