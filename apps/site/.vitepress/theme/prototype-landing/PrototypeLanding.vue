<script setup lang="ts">
// PROTOTYPE (#87). Three landing variants of the same storyboard on one route.
import { onMounted, ref } from 'vue'
import VariantA from './VariantA.vue'
import VariantB from './VariantB.vue'
import VariantC from './VariantC.vue'
import PrototypeSwitcher from './PrototypeSwitcher.vue'
import './proto.css'

const VARIANTS = [
  { key: 'A', name: '长镜头' },
  { key: 'B', name: '短片' },
  { key: 'C', name: '分章小场景' },
]
const variant = ref<string | null>(null)

function choose(key: string) {
  const url = new URL(window.location.href)
  url.searchParams.set('variant', key)
  window.history.replaceState(null, '', url)
  variant.value = key
  window.scrollTo({ top: 0 })
}

onMounted(() => {
  const v = new URL(window.location.href).searchParams.get('variant')?.toUpperCase()
  variant.value = VARIANTS.some((x) => x.key === v) ? v! : 'A'
})
</script>

<template>
  <ClientOnly>
    <VariantA v-if="variant === 'A'" :key="'A'" />
    <VariantB v-else-if="variant === 'B'" :key="'B'" />
    <VariantC v-else-if="variant === 'C'" :key="'C'" />
    <PrototypeSwitcher v-if="variant" :variants="VARIANTS" :current="variant" @change="choose" />
  </ClientOnly>
</template>
