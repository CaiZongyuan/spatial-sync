<script setup lang="ts">
import { STATUS_LABEL, type Status } from './story'
defineProps<{ status: Status }>()
</script>

<template>
  <span class="pl-chip" :class="`pl-chip--${status}`"><i />{{ STATUS_LABEL[status] }}</span>
</template>
